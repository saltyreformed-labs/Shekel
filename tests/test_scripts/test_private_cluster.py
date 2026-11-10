"""The throwaway cluster's one home starts a cluster and stops what carries its token.

``scripts/private_cluster.py`` holds the life of the cluster ``scripts/test.sh`` gives every
suite run, and that the release rehearsal kit is to start (step X-cx's K1a).  A cluster is
owned by a TOKEN its caller makes: the start labels the container with it as docker creates
it, and the stop removes only what carries it (the coordinator's ruling on K1a commit B,
2026-10-19).  These drive the module with a stub ``docker`` on ``PATH`` that keeps its
containers' names and labels, so they need no daemon, no image and no database; the
wrapper's own cases (the ``test_test_runner`` package) drive the same module through
``test.sh``.  What a REAL cluster does is graded by every suite run, which starts one here.
"""
from __future__ import annotations

import itertools
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import private_cluster

# Fixed and plainly not secrets: a token is any 32 lowercase hex digits.
_TOKEN = "01" * 16
_OTHER_TOKEN = "fe" * 16
_NAME = "shekel-testrun-4242-01010101"
_IMAGE = "stub-image:test"
_MODULE = Path(private_cluster.__file__)
_DIRECTORY = f"shekel-cluster-{_TOKEN}"
_LABEL = f"shekel.private_cluster.token={_TOKEN}"
_LISTED = f"ps --all --quiet --filter label={_LABEL}"
# What a start asks the daemon before it makes anything (``private_cluster.daemon``).
_ASKED = "info --format {{.SecurityOptions}}"
# The id the stub gives the container a start makes.
_MADE = "c0ffee"
_CONTAINER_LEFT = (f"container {_MADE} of cluster {_TOKEN} survived teardown -- remove it by "
                   f"hand ('docker rm -fv {_MADE}').")


@dataclass(frozen=True)
class _Behaviour:
    """How the stub ``docker`` behaves; each default is a daemon that works.

    ``run`` refuses a name a container already holds, as docker does ("Conflict"), and
    otherwise makes the container ``c0ffee``, keeping its name and its ``--label``, and,
    unless ``socket`` is ``"missing"``, the socket in the directory mounted at ``/sockets``
    as postgres would; ``socket="pinned"`` then takes the write bit off that directory, so
    the socket cannot be unlinked and the directory survives a stop.  Given a ``run_error``,
    ``run`` instead writes those bytes on stderr and exits 125, making nothing.  ``ps``
    lists the ids of the containers whose label is exactly the one its filter names (any
    value, given only the key, as docker's filter does), or exits 1 as an ``unreachable``
    daemon does; ``rm`` removes the containers it is given by id or by name unless
    ``survives``; ``exec`` (the readiness probe, or ``client``'s tool) answers ``ready``, and
    given ``-i`` first echoes its stdin and writes on stderr the daemon it was handed
    (``DOCKER_HOST``); ``info`` says the daemon is rootless unless ``rootless`` is False.  The
    command
    named ``slow`` takes 2 seconds; a ``run`` CLI signalled in that wait exits at once and
    its create lands 0.5 s later, as the daemon finishes a create its CLI abandoned (the
    delta review of bb2f1492a's MEDIUM-A).
    """

    run_error: bytes = b""
    ready: bool = True
    socket: str = "made"
    survives: bool = False
    unreachable: bool = False
    slow: str = ""
    rootless: bool = True


_WORKS = _Behaviour()


@dataclass(frozen=True)
class _Stub:
    """A case's stub ``docker``: it lives in ``bin``, records each command it is given in
    ``docker-calls``, and holds each container as a file in ``containers`` named by its id,
    holding its name and its label."""

    home: Path

    @property
    def binaries(self) -> Path:
        """The directory the stub is put first on ``PATH`` from."""
        return self.home / "bin"

    @property
    def containers(self) -> Path:
        """The directory holding one file per container the stub's daemon holds."""
        return self.home / "containers"

    @property
    def made(self) -> Path:
        """The file that stands for the container a start makes, while it exists."""
        return self.containers / _MADE

    def hold(self, container: str, name: str, label: str = "") -> Path:
        """Give the daemon a container no start of this case made; its file."""
        held = self.containers / container
        held.write_text(f"{name}\n{label}", encoding="utf-8")
        return held

    def calls(self) -> list[str]:
        """Every command the stub was given, in order; none if it was never run."""
        recorded = self.home / "docker-calls"
        if not recorded.exists():
            return []
        return recorded.read_text(encoding="utf-8", errors="surrogateescape").splitlines()

    def write(self, behaviour: _Behaviour) -> None:
        """Write the stub ``docker``, behaving as ``behaviour`` says."""
        self.binaries.mkdir()
        self.containers.mkdir()
        bind = ("        listener.bind(os.path.join(host, '.s.PGSQL.5432'))"
                if behaviour.socket != "missing" else "        pass")
        recorded = self.home / "docker-calls"
        (self.binaries / "docker").write_text(
            "#!/usr/bin/env python3\n"
            "import os, signal, socket, sys, time\n"
            "args = sys.argv[1:]\n"
            # surrogateescape: a socket path may hold a byte that is not UTF-8.
            f"open({str(recorded)!r}, 'a', encoding='utf-8', errors='surrogateescape')"
            ".write(' '.join(args) + chr(10))\n"
            "cmd = args[0] if args else ''\n"
            f"box = {str(self.containers)!r}\n"
            "def option(flag):\n"
            "    return args[args.index(flag) + 1]\n"
            "def held():\n"
            "    return {c: open(os.path.join(box, c)).read().split(chr(10))\n"
            "            for c in os.listdir(box)}\n"
            "def create():\n"
            f"    open(os.path.join(box, {_MADE!r}), 'w').write(\n"
            "        option('--name') + chr(10) + option('--label'))\n"
            "    for mount in [args[i + 1] for i, a in enumerate(args) if a == '-v']:\n"
            "        host = mount.rsplit(':', 1)[0]\n"
            "        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
            # Bound and deliberately NOT unlinked: the file must outlive the stub.
            f"{bind}\n"
            f"        if {behaviour.socket == 'pinned'!r}:\n"
            "            os.chmod(host, 0o555)\n"
            "def abandoned(signum, _frame):\n"
            "    if os.fork() == 0:\n"
            # The daemon's side holds none of the CLI's pipes, so the caller is not kept
            # waiting for it.
            "        os.close(1)\n"
            "        os.close(2)\n"
            "        time.sleep(0.5)\n"
            "        create()\n"
            "        os._exit(0)\n"
            "    sys.exit(128 + signum)\n"
            f"if cmd == {behaviour.slow!r}:\n"
            "    if cmd == 'run':\n"
            "        signal.signal(signal.SIGINT, abandoned)\n"
            "        signal.signal(signal.SIGTERM, abandoned)\n"
            "    time.sleep(2)\n"
            "if cmd == 'run':\n"
            f"    if {behaviour.run_error!r}:\n"
            f"        sys.stderr.buffer.write({behaviour.run_error!r})\n"
            "        sys.exit(125)\n"
            "    if any(n == option('--name') for n, _ in held().values()):\n"
            "        print('Conflict. The container name is already in use', file=sys.stderr)\n"
            "        sys.exit(125)\n"
            "    create()\n"
            f"    print({_MADE!r})\n"
            # ``rm`` takes an id or a name, as docker does.
            "elif cmd == 'rm':\n"
            "    for c, (name, _) in held().items():\n"
            f"        if (c in args[2:] or name in args[2:]) and not {behaviour.survives!r}:\n"
            "            os.remove(os.path.join(box, c))\n"
            "elif cmd == 'ps':\n"
            f"    if {behaviour.unreachable!r}:\n"
            "        print('Cannot connect to the Docker daemon', file=sys.stderr)\n"
            "        sys.exit(1)\n"
            # A filter naming only the label's key lists every value, as docker's does.
            "    wanted = option('--filter').split('=', 1)[1]\n"
            "    for c, (_, label) in held().items():\n"
            "        if wanted in (label, label.split('=', 1)[0]):\n"
            "            print(c)\n"
            "elif cmd == 'exec':\n"
            "    if '-i' in args:\n"
            "        sys.stdout.buffer.write(sys.stdin.buffer.read())\n"
            "        sys.stderr.write(os.environ.get('DOCKER_HOST', ''))\n"
            f"    sys.exit(0 if {behaviour.ready!r} else 1)\n"
            "elif cmd == 'info':\n"
            f"    print({'[name=rootless]' if behaviour.rootless else '[name=seccomp]'!r})\n"
            "sys.exit(0)\n",
            encoding="utf-8",
        )
        (self.binaries / "docker").chmod(0o755)


@pytest.fixture(name="runtime", autouse=True)
def _runtime(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A SHORT throwaway ``XDG_RUNTIME_DIR`` for EVERY case, so none can reach the real one:
    the socket directory is made under it, and pytest's ``tmp_path`` alone is most of a
    socket path's 107-byte budget.  The readiness cap is 0, so a stub that is not ready
    fails the wait at once; a ready one answers the first probe, which no cap cuts short."""
    runtime = Path(tempfile.mkdtemp(prefix="skrt", dir="/tmp"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("READINESS_TIMEOUT_SECONDS", "0")
    yield runtime
    shutil.rmtree(runtime, ignore_errors=True)


@pytest.fixture(name="docker")
def _docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., _Stub]:
    """Put a stub ``docker`` first on ``PATH``, behaving as the case asks; the stub."""

    def install(behaviour: _Behaviour = _WORKS) -> _Stub:
        """Write the stub ``docker`` and put it first on ``PATH``; the stub."""
        stub = _Stub(tmp_path)
        stub.write(behaviour)
        monkeypatch.setenv("PATH", f"{stub.binaries}:/usr/bin:/bin")
        return stub

    return install


def _unpin(directory: Path) -> None:
    """Give a directory a stub pinned back its write bit, so the case's cleanup removes it."""
    if directory.exists():
        directory.chmod(0o700)


def test_a_started_cluster_is_reached_by_its_socket_and_stopping_it_leaves_nothing(
        docker, runtime, capfd):
    """``start`` makes the token's socket directory open to the container (0777), starts the
    container labelled with the token, probes it by its id over ``/sockets`` as the image's user
    (``client``'s spelling, its stdin not attached) and prints the
    socket directory, then the container's id; the socket is in it.  ``stop`` lists what
    carries the token, removes it by id with its volume, lists again, removes the directory,
    names nothing and exits 0.  Before its run, the start asks docker only what daemon it is.

    The two lines are read from the process's file descriptor 1, where ``test.sh`` reads
    them: the start writes them there as bytes, never through ``sys.stdout`` (K1a commit B3)."""
    stub = docker()
    assert private_cluster.main(["start", _NAME, _TOKEN, _IMAGE]) == 0
    assert capfd.readouterr().out.splitlines() == [str(runtime / _DIRECTORY), _MADE]
    assert (runtime / _DIRECTORY / ".s.PGSQL.5432").is_socket()
    assert (runtime / _DIRECTORY).stat().st_mode & 0o777 == 0o777
    assert private_cluster.main(["stop", _TOKEN]) == 0
    assert capfd.readouterr().err == ""
    assert not (runtime / _DIRECTORY).exists()
    assert not stub.made.exists()
    calls = stub.calls()
    assert calls[0] == _ASKED and calls[1].startswith("run ") and calls[2:] == [
        f"exec -e PGHOST=/sockets -e PGUSER=shekel_user {_MADE} pg_isready -q", _LISTED,
        f"rm -fv {_MADE}", _LISTED]


def test_a_token_is_new_each_time_and_is_one_a_start_takes(capsys):
    """``token`` prints 32 lowercase hex digits, a new one each time, and a start accepts it
    (its socket directory is named after it)."""
    assert private_cluster.main(["token"]) == 0
    assert private_cluster.main(["token"]) == 0
    first, second = capsys.readouterr().out.split()
    assert first != second
    for token in (first, second):
        assert len(token) == 32 and set(token) <= set("0123456789abcdef"), token
        assert private_cluster.socket_dir(token).name == f"shekel-cluster-{token}"


def test_url_names_the_database_on_the_socket_directory_as_the_cluster_s_user(capsys):
    """``url`` prints the URL ``test.sh`` spelled itself until fork B (2026-10-19), byte for
    byte: the baked image's user and password, the database, the socket directory as host."""
    assert private_cluster.main(["url", "/run/user/1000/shekel-cluster-ab", "shekel_test"]) == 0
    assert capsys.readouterr().out == (
        "postgresql://shekel_user:shekel_pass@/shekel_test"
        "?host=/run/user/1000/shekel-cluster-ab\n")


@pytest.mark.parametrize("ready", [True, False], ids=["the tool succeeds", "the tool fails"])
def test_client_becomes_the_tool_in_the_container_its_bytes_and_status_the_tool_s(
        docker, runtime, ready):
    """``client`` runs a client tool INSIDE the container at its socket as the image's user,
    stdin attached (``balance:R-BAL327``).  The process becomes that ``docker exec``, so the
    bytes it reads reach the tool, its stdout and exit status are the tool's, and docker is
    handed the daemon a start goes to: here the rootless socket in ``XDG_RUNTIME_DIR``, with
    ``DOCKER_HOST`` unset.  Run as a command, since it replaces the process it runs in."""
    stub = docker(_Behaviour(ready=ready))
    rootless = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    rootless.bind(str(runtime / "docker.sock"))
    try:
        ran = subprocess.run(
            [sys.executable, str(_MODULE), "client", "--", _MADE, "pg_restore", "--list"],
            input=b"\x00a dump\xff", capture_output=True, check=False, timeout=30,
            env={name: value for name, value in os.environ.items() if name != "DOCKER_HOST"})
    finally:
        rootless.close()
    assert (ran.returncode, ran.stdout, ran.stderr) == (
        0 if ready else 1, b"\x00a dump\xff", os.fsencode(f"unix://{runtime}/docker.sock"))
    assert stub.calls() == [
        f"exec -i -e PGHOST=/sockets -e PGUSER=shekel_user {_MADE} pg_restore --list"]


def test_client_names_a_docker_it_cannot_run_and_exits_2(tmp_path, monkeypatch):
    """A ``docker`` that cannot be executed is named, exit 2, never a traceback: ``PATH`` holds
    ONLY that one, so the real docker is never reached."""
    (tmp_path / "docker").write_text("#!/bin/sh\n", encoding="utf-8")
    (tmp_path / "docker").chmod(0o644)
    monkeypatch.setenv("PATH", str(tmp_path))
    ran = subprocess.run([sys.executable, str(_MODULE), "client", _MADE, "psql"],
                         capture_output=True, text=True, check=False, timeout=30)
    assert (ran.returncode, ran.stdout, ran.stderr) == (2, "", (
        f"[private_cluster] docker cannot be run: Permission denied: {tmp_path}/docker\n"))


def test_the_run_is_the_one_the_suite_reads_argument_for_argument(docker, runtime):
    """The whole ``docker run``, as one literal: detached, removed when postgres exits, the
    name, the token's label given to the create itself, no network and no published port,
    the baked image's user, password, database and PGDATA, the token's socket directory
    mounted at ``/sockets`` (never the entrypoint's own ``/var/run/postgresql``), and every
    ``-c`` setting the suite relies on, the non-durable three among them."""
    stub = docker()
    private_cluster.start(_NAME, _TOKEN, _IMAGE)
    assert stub.calls()[1].split() == [
        "run", "-d", "--rm", "--name", _NAME, "--label", _LABEL, "--network=none",
        "-e", "POSTGRES_USER=shekel_user", "-e", "POSTGRES_PASSWORD=shekel_pass",
        "-e", "POSTGRES_DB=postgres", "-e", "PGDATA=/pgdata-baked",
        "-v", f"{runtime / _DIRECTORY}:/sockets", _IMAGE,
        "-c", "lock_timeout=10000", "-c", "statement_timeout=30000",
        "-c", "fsync=off", "-c", "synchronous_commit=off",
        "-c", "full_page_writes=off", "-c", "max_locks_per_transaction=256",
        "-c", "listen_addresses=", "-c", "unix_socket_directories=/sockets"]


@pytest.mark.parametrize(("behaviour", "said"), [
    (_Behaviour(run_error=b"no such image"),
     f"docker could not start {_NAME} from {_IMAGE}: no such image"),
    (_Behaviour(run_error=b"no such image \xff"),
     f"docker could not start {_NAME} from {_IMAGE}: no such image �"),
    (_Behaviour(ready=False), f"{_NAME} did not accept connections within 0s"),
    (_Behaviour(socket="missing"), f"{_NAME} reported ready but left no socket at "),
], ids=["docker refuses", "docker says a byte that is not UTF-8", "never ready",
        "ready without a socket"])
def test_a_start_that_fails_stops_what_it_started(docker, runtime, capsys, behaviour, said):
    """Each way a start can fail once it has begun making the cluster exits 2 naming it, and
    leaves no container and no socket directory: the start stops what carries its token
    before it reports.  A byte docker prints that is not UTF-8 is named as U+FFFD: decoded
    strictly, it raised an error that is no ClusterError, so the start skipped its own stop
    and ended in a traceback (the review of 668b53c99)."""
    stub = docker(behaviour)
    assert private_cluster.main(["start", _NAME, _TOKEN, _IMAGE]) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err.startswith(f"[private_cluster] {said}")
    assert "left something behind" not in out.err
    assert _LISTED in stub.calls()
    assert not (runtime / _DIRECTORY).exists()
    assert not stub.made.exists()


@pytest.mark.parametrize("socket_kind", ["made", "pinned"],
                         ids=["the container", "the container and the directory"])
def test_a_failed_start_names_what_its_own_stop_left_behind(docker, runtime, capsys,
                                                            socket_kind):
    """The review's MEDIUM-2: a start that fails and then cannot remove what it made names
    the failure AND every survivor, never the failure alone, so a caller that trusts its
    exit 2 is told the cluster is still there; with two survivors, both are named."""
    docker(_Behaviour(ready=False, survives=True, socket=socket_kind))
    left = (f"{_CONTAINER_LEFT} {runtime / _DIRECTORY} survived teardown."
            if socket_kind == "pinned" else _CONTAINER_LEFT)
    try:
        assert private_cluster.main(["start", _NAME, _TOKEN, _IMAGE]) == 2
        assert capsys.readouterr().err == (
            f"[private_cluster] {_NAME} did not accept connections within 0s.  Stopping it "
            f"left something behind: {left}\n")
    finally:
        _unpin(runtime / _DIRECTORY)


def test_a_name_another_container_holds_fails_the_run_and_that_container_is_left(
        docker, runtime, capsys):
    """S10 of the commit B design: a container this start did not make already holds the
    name (it carries no label, as one made outside this module does not).  Docker refuses
    the run ("Conflict"), the start exits 2 naming it, and its stop removes nothing of that
    container, which does not carry the token: a stop by NAME removed it."""
    stub = docker()
    other = stub.hold("bead01", _NAME)
    assert private_cluster.main(["start", _NAME, _TOKEN, _IMAGE]) == 2
    assert capsys.readouterr().err == (
        f"[private_cluster] docker could not start {_NAME} from {_IMAGE}: Conflict. The "
        "container name is already in use\n")
    assert other.exists()
    assert not [call for call in stub.calls() if call.startswith("rm ")], stub.calls()
    assert not (runtime / _DIRECTORY).exists()


def test_stop_removes_only_what_carries_its_token(docker, runtime):
    """The coordinator's MEDIUM-1 invariant, where the ruling on commit B put it: a stop
    NEVER removes a container its token's start did not make.  Beside this cluster's own
    container, the daemon holds one of the same name with no label and one labelled with
    another token; the stop removes its own and neither of the others, and its socket
    directory but not the other token's."""
    stub = docker()
    private_cluster.start(_NAME, _TOKEN, _IMAGE)
    unlabelled = stub.hold("bead01", _NAME)
    another = stub.hold("bead02", "shekel-testrun-4242-fefefefe",
                        f"shekel.private_cluster.token={_OTHER_TOKEN}")
    (runtime / f"shekel-cluster-{_OTHER_TOKEN}").mkdir()
    assert not private_cluster.stop(_TOKEN)
    assert not stub.made.exists()
    assert unlabelled.exists() and another.exists()
    assert (runtime / f"shekel-cluster-{_OTHER_TOKEN}").exists()
    assert not (runtime / _DIRECTORY).exists()


@pytest.mark.parametrize("token", ["../../escape", "0123456789ABCDEF0123456789ABCDEF",
                                   "0123456789abcdef", ""],
                         ids=["a path", "upper case", "too short", "empty"])
def test_a_malformed_token_is_refused_before_anything_is_asked_or_removed(
        docker, runtime, capsys, token):
    """A token becomes a path component the stop removes, so one that is not 32 lowercase
    hex digits is refused by ``start`` and by ``stop`` (exit 2, named) before docker is asked
    anything and before any path is touched: ``../../escape`` would point the removal
    outside ``XDG_RUNTIME_DIR``."""
    stub = docker()
    (runtime / "kept").mkdir()
    assert private_cluster.main(["start", _NAME, token, _IMAGE]) == 2
    assert private_cluster.main(["stop", token]) == 2
    assert capsys.readouterr().err.count(f"{token!r} is not a cluster token") == 2
    assert stub.calls() == []
    assert (runtime / "kept").exists()


@pytest.mark.parametrize("answer", ["not rootless", "cannot be asked"])
def test_a_daemon_that_is_not_rootless_is_refused_before_anything_is_made(
        docker, runtime, monkeypatch, capsys, answer):
    """The coordinator's ruling 1 on K1a-2's review (B1): the refusal lives in the act that
    starts a cluster, so no caller can start one on the system daemon, which runs production.
    A daemon that says it is not rootless, or cannot say what it is, with neither sanction
    set, fails the start (exit 2) naming why and the way past it, having asked docker only
    that: no socket directory, no run.  ``CI`` and ``SHEKEL_ALLOW_HOST_DOCKER`` are cleared,
    since CI sets the first and either would sanction the daemon."""
    stub = docker(_Behaviour(rootless=False, unreachable=answer == "cannot be asked"))
    if answer == "cannot be asked":
        (stub.binaries / "docker").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("SHEKEL_ALLOW_HOST_DOCKER", raising=False)
    assert private_cluster.main(["start", _NAME, _TOKEN, _IMAGE]) == 2
    said = {"not rootless": ("is not rootless", "SHEKEL_ALLOW_HOST_DOCKER=1 to accept"),
            "cannot be asked": ("could not ask", "systemctl --user start docker.service")}
    err = capsys.readouterr().err
    assert all(part in err for part in said[answer]), err
    assert stub.calls() == ([_ASKED] if answer == "not rootless" else [])
    assert not (runtime / _DIRECTORY).exists()


@pytest.mark.parametrize("kind", ["a file", "a symlink"])
def test_anything_already_at_the_socket_directory_s_path_is_refused_never_taken_over(
        docker, runtime, capsys, kind):
    """The token's socket directory is made EXCLUSIVELY: a file or a symlink already at its
    path fails the start (exit 2, named) before docker is asked to run anything, and a
    symlink's target is never opened to the container: its mode and what it holds are kept.
    A token is new, so nothing a start of its cluster made can be there."""
    stub = docker()
    target = runtime / "elsewhere"
    target.mkdir(mode=0o700)
    (target / "kept").write_text("", encoding="utf-8")
    if kind == "a file":
        (runtime / _DIRECTORY).write_text("", encoding="utf-8")
    else:
        (runtime / _DIRECTORY).symlink_to(target)
    assert private_cluster.main(["start", _NAME, _TOKEN, _IMAGE]) == 2
    assert capsys.readouterr().err.startswith(
        f"[private_cluster] socket directory {runtime / _DIRECTORY} cannot be made: [Errno 17] "
        "File exists")
    assert not [call for call in stub.calls() if call.startswith("run ")], stub.calls()
    assert (target / "kept").exists()
    assert target.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("blocked", ["runtime", "a file"],
                         ids=["an unwritable runtime directory",
                              "a runtime directory that is a file"])
def test_a_socket_directory_that_cannot_be_made_is_named_never_a_traceback(
        docker, runtime, monkeypatch, capsys, blocked):
    """A socket directory that cannot be made -- under an ``XDG_RUNTIME_DIR`` this user cannot
    write, or one that is a FILE -- exits 2 naming the path and why; it never escapes as an
    exception, and docker is never asked to run anything.  The file's error is no
    PermissionError (delta review 2's nit 1)."""
    stub = docker()
    said = "[Errno 13] Permission denied"
    home = runtime
    if blocked == "a file":
        said = "[Errno 20] Not a directory"
        home = runtime / "a-file"
        home.write_text("", encoding="utf-8")
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(home))
    else:
        runtime.chmod(0o500)
    held = home / _DIRECTORY
    try:
        assert private_cluster.main(["start", _NAME, _TOKEN, _IMAGE]) == 2
        err = capsys.readouterr().err
    finally:
        runtime.chmod(0o700)
    assert err == f"[private_cluster] socket directory {held} cannot be made: {said}: '{held}'\n"
    assert stub.calls() == [_ASKED, _LISTED]


def _wait_for(stub: _Stub, command: str) -> None:
    """Wait until the stub has been given ``command``, or fail the case."""
    deadline = time.monotonic() + 20
    while not any(call.split()[0] == command for call in stub.calls()):
        if time.monotonic() >= deadline:
            pytest.fail(f"the start never reached docker {command}: {stub.calls()}")
        time.sleep(0.05)


def _start_in_a_shell(tail: str = "") -> subprocess.Popen[str]:
    """Run the module's ``start`` from a shell that leads a session of its own, as a terminal
    runs a command, then ``tail``; the shell.

    The case's own process may have SIGINT IGNORED (a backgrounded pytest inherits that),
    and an ignored signal is inherited across exec: bash cannot take back one ignored when
    it started, and Python leaves it ignored, so ``env --default-signal`` gives the shell the
    default first.
    """
    return subprocess.Popen(
        ["env", "--default-signal=INT", "bash", "-c",
         '"$0" "$1" start "$2" "$3" "$4"' + tail,
         sys.executable, str(_MODULE), _NAME, _TOKEN, _IMAGE],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
        env={**os.environ, "READINESS_TIMEOUT_SECONDS": "30"})


def _ended(shell: subprocess.Popen[str], runtime: Path) -> tuple[str, str]:
    """Wait for ``shell``; its stdout and stderr.  It is killed if it outlives the wait."""
    try:
        return shell.communicate(timeout=30)
    finally:
        if shell.poll() is None:
            os.killpg(shell.pid, signal.SIGKILL)
            shell.communicate()
        _unpin(runtime / _DIRECTORY)


_WARNED_CONTAINER = f"[private_cluster] WARNING: {_CONTAINER_LEFT}\n"


@pytest.mark.parametrize(("behaviour", "warned"), [
    (_Behaviour(ready=False), ""),
    (_Behaviour(ready=False, survives=True), _WARNED_CONTAINER),
    (_Behaviour(ready=False, survives=True, socket="pinned"),
     _WARNED_CONTAINER + "[private_cluster] WARNING: {directory} survived teardown.\n"),
], ids=["during the readiness wait", "its stop leaves the container",
        "its stop leaves the container and the directory"])
def test_ctrl_c_ends_a_start_by_sigint_once_it_has_stopped_what_it_made(
        docker, runtime, behaviour, warned):
    """The review's LOW-5 and the delta review's LOW-2: Ctrl-C to the start's whole process
    group, as a terminal sends it, stops what the start made, NAMES each thing that stop
    could not remove, and ends the start BY SIGINT with no traceback, so the shell that ran
    it stops too: an exit status of 130 tells a shell the child dealt with the Ctrl-C, and it
    went on to its next command.  (A Ctrl-C before anything is made, while the start asks the
    daemon what it is, ends it as Python ends on one: there is nothing to stop.)"""
    stub = docker(behaviour)
    with _start_in_a_shell('; echo "carried on: $?"') as shell:
        _wait_for(stub, "exec")
        os.killpg(shell.pid, signal.SIGINT)
        out, err = _ended(shell, runtime)
    assert (shell.returncode, out, err) == (
        -signal.SIGINT, "", warned.format(directory=runtime / _DIRECTORY))
    assert stub.made.exists() is behaviour.survives
    assert (runtime / _DIRECTORY).exists() is (behaviour.socket == "pinned")
    assert f"rm -fv {_MADE}" in stub.calls()


def test_ctrl_c_while_docker_run_is_in_flight_leaves_nothing(docker, runtime):
    """MEDIUM-A of the delta review of bb2f1492a: Ctrl-C to the start's whole process group
    while ``docker run`` is in flight.  Had it reached the run's CLI, the CLI would have
    exited and the daemon finished the create AFTER the start's stop had looked (the stub's
    slow ``run`` does exactly that), leaving a container nothing names.  The CLI runs in a
    session of its own, so the Ctrl-C reaches only the start, which holds it until the run
    returns, then stops what it made and ends by SIGINT; a second later nothing is left."""
    stub = docker(_Behaviour(slow="run"))
    with _start_in_a_shell() as shell:
        _wait_for(stub, "run")
        os.killpg(shell.pid, signal.SIGINT)
        _ended(shell, runtime)
    time.sleep(1)
    assert shell.returncode == -signal.SIGINT
    assert not list(stub.containers.iterdir()), stub.calls()
    assert not (runtime / _DIRECTORY).exists()


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP], ids=["TERM", "HUP"])
def test_term_or_hup_to_the_start_is_held_until_docker_run_returns(docker, runtime, signum):
    """TERM or HUP to the start's process alone, as a supervisor or a closing terminal sends
    it, while ``docker run`` is in flight: the start holds it until the run returns, stops
    what it made and ends BY that signal.  At the default disposition the start died at once
    and its run's CLI went on to make a container nothing would stop."""
    stub = docker(_Behaviour(slow="run"))
    with subprocess.Popen([sys.executable, str(_MODULE), "start", _NAME, _TOKEN, _IMAGE],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          start_new_session=True) as start:
        _wait_for(stub, "run")
        start.send_signal(signum)
        _ended(start, runtime)
    # Past the stub run's 2 s, so a CLI the start abandoned has made its container.
    time.sleep(2.5)
    assert start.returncode == -signum
    assert not list(stub.containers.iterdir()), stub.calls()
    assert f"rm -fv {_MADE}" in stub.calls()


def test_a_signal_that_meets_a_failure_ends_the_start_by_the_signal(docker, runtime):
    """A Ctrl-C held while ``docker run`` is in flight, and the run then fails: the start
    stops what carries its token and ends BY SIGINT, not with the failure's exit 2, since
    its caller asked it to end; the failure is not reported over the signal."""
    stub = docker(_Behaviour(slow="run", run_error=b"no such image"))
    with _start_in_a_shell() as shell:
        _wait_for(stub, "run")
        os.killpg(shell.pid, signal.SIGINT)
        out, err = _ended(shell, runtime)
    assert (shell.returncode, out, err) == (-signal.SIGINT, "", "")
    assert _LISTED in stub.calls()


def test_a_second_ctrl_c_inside_the_start_s_own_stop_lets_the_stop_finish(docker, runtime):
    """A Ctrl-C during the readiness wait starts the start's stop, and a second one lands
    while its slow ``rm`` runs.  The stop is HELD too, so it finishes: after the removal it
    lists again (the call that names a survivor), and the start ends by SIGINT with nothing
    left and no traceback.  Before, a second Ctrl-C ended a stop half-done, unnamed."""
    stub = docker(_Behaviour(ready=False, slow="rm"))
    with _start_in_a_shell() as shell:
        _wait_for(stub, "exec")
        os.killpg(shell.pid, signal.SIGINT)
        _wait_for(stub, "rm")
        os.killpg(shell.pid, signal.SIGINT)
        out, err = _ended(shell, runtime)
    assert (shell.returncode, out, err) == (-signal.SIGINT, "", "")
    calls = stub.calls()
    assert calls[calls.index(f"rm -fv {_MADE}") + 1:] == [_LISTED], calls
    assert not stub.made.exists()
    assert not (runtime / _DIRECTORY).exists()


def test_ctrl_c_reaches_an_in_process_caller_as_a_keyboard_interrupt(docker, monkeypatch):
    """Delta review 2's nit 2: Ctrl-C during a start that runs IN PROCESS, as these cases
    run it under pytest, reaches the caller as a KeyboardInterrupt and never an
    ``Exception``, carrying the signal and what the start's stop could not remove.  Measured
    2026-10-18 with a two-case file: pytest stops its session on a KeyboardInterrupt, and
    records an ``Exception`` as one failed case and goes on to the next.  The Ctrl-C is
    raised where the readiness wait sleeps, as an in-process caller's own handler raises it."""
    stub = docker(_Behaviour(ready=False, survives=True))
    monkeypatch.setenv("READINESS_TIMEOUT_SECONDS", "30")

    def ctrl_c(_seconds: float) -> None:
        """Raise what a Ctrl-C raises."""
        raise KeyboardInterrupt

    monkeypatch.setattr(private_cluster, "time",
                        SimpleNamespace(monotonic=time.monotonic, sleep=ctrl_c))
    with pytest.raises(KeyboardInterrupt) as interrupted:
        private_cluster.start(_NAME, _TOKEN, _IMAGE)
    assert isinstance(interrupted.value, private_cluster.Interrupted)
    assert not isinstance(interrupted.value, Exception)
    assert interrupted.value.args == (_CONTAINER_LEFT,)
    assert interrupted.value.signum == signal.SIGINT
    assert f"rm -fv {_MADE}" in stub.calls()


def test_a_signal_after_the_start_s_last_look_reaches_its_caller(docker, monkeypatch):
    """A signal that lands after the start's last look at its caller's signals, as it hands
    the cluster over (here while it checks the socket), is not lost: once the caller's own
    handlers are back it is raised again under them, and the cluster is the caller's."""
    docker()
    caught: list[int] = []
    socket_kind = private_cluster.stat.S_ISSOCK

    def ctrl_c_then(mode: int) -> bool:
        """Send this process a Ctrl-C, then answer as the real check does."""
        os.kill(os.getpid(), signal.SIGINT)
        return socket_kind(mode)

    monkeypatch.setattr(private_cluster, "stat", SimpleNamespace(S_ISSOCK=ctrl_c_then))
    before = signal.signal(signal.SIGINT, lambda signum, _frame: caught.append(signum))
    try:
        cluster = private_cluster.start(_NAME, _TOKEN, _IMAGE)
    finally:
        signal.signal(signal.SIGINT, before)
    assert caught == [signal.SIGINT]
    assert cluster.container == _MADE


def _unwritable(kind: str) -> int:
    """A file descriptor nothing can be written to, for a start's stdout; the caller closes
    it.  ``"a pipe nobody reads"`` is a pipe whose read end is already closed, as a caller
    KILLed while it waited leaves one; ``"a full disk"`` is ``/dev/full``."""
    if kind == "a full disk":
        return os.open("/dev/full", os.O_WRONLY)
    reader, writer = os.pipe()
    os.close(reader)
    return writer


# The module's start as a command, and the same command run with NO descriptor 1 at all: a
# caller that closed its own stdout first (``1>&-``), which leaves ``sys.stdout`` None.
_START = [sys.executable, str(_MODULE), "start", _NAME, _TOKEN, _IMAGE]
_START_WITHOUT_STDOUT = ["bash", "-c", 'exec 1>&-; exec "$@"', "-", *_START]


@pytest.mark.parametrize(("command", "unwritable", "said"), [
    (_START, "a pipe nobody reads", "EPIPE (Broken pipe)"),
    (_START, "a full disk", "ENOSPC (No space left on device)"),
    (_START_WITHOUT_STDOUT, "a pipe nobody reads", "EBADF (Bad file descriptor)"),
], ids=["a pipe nobody reads", "a full disk", "no stdout at all"])
def test_a_start_that_cannot_hand_its_cluster_over_stops_it_and_exits_2(
        docker, runtime, command, unwritable, said):
    """K1a commit B3 (T10): a start whose cluster accepts connections but whose two lines
    cannot be written stops what carries its token and exits 2, naming the write's own error,
    with no traceback: whoever started it cannot read the cluster, so nothing else will stop
    it.  Any write that fails is one: to a closed pipe (its caller gone), to a full disk, or
    with no stdout at all.  On commit B2 the lines sat in stdout's buffer until the
    interpreter's exit, where the write failed after the start had returned ("Exception
    ignored", exit 120), and both were left; with no stdout, B3's first draft, which flushed
    ``sys.stdout``, ended in an AttributeError traceback, exit 1, and left both too."""
    stub = docker()
    out = _unwritable(unwritable)
    try:
        started = subprocess.run(command, stdout=out, stderr=subprocess.PIPE, text=True,
                                 check=False, timeout=30)
    finally:
        os.close(out)
    assert (started.returncode, started.stderr) == (
        2, f"[private_cluster] container {_MADE} accepts connections but could not be handed "
           f"over: {said}\n")
    assert stub.calls()[-2:] == [f"rm -fv {_MADE}", _LISTED]
    assert not stub.made.exists()
    assert not (runtime / _DIRECTORY).exists()


def test_the_handover_is_the_socket_directory_s_own_bytes_whatever_the_output_encoding(
        docker, runtime):
    """The handover writes the socket directory's own bytes (``os.fsencode``), whatever
    encoding Python's text output has: here ``PYTHONIOENCODING=ascii`` and a socket directory
    under a directory named U+00E9 (an accented e), which an ASCII text stream cannot spell
    (it raises UnicodeEncodeError, which is no OSError).  The start succeeds, and its stdout
    is exactly the directory's bytes, then the container's id: the claim of the
    coordinator's ruling (a') on commit B3 that this class of failure is gone.

    Compared as bytes, whose repr is ASCII: a failure message holding the path itself cannot
    be sent between xdist's processes (the not-UTF-8 case below says so)."""
    stub = docker()
    accented = runtime / "é"
    started = subprocess.run(_START, capture_output=True, check=False, timeout=30,
                             env={**os.environ, "XDG_RUNTIME_DIR": str(accented),
                                  "PYTHONIOENCODING": "ascii"})
    assert (started.returncode, started.stderr) == (0, b"")
    assert started.stdout == os.fsencode(accented / _DIRECTORY) + b"\n" + _MADE.encode() + b"\n"
    assert stub.made.exists()


def test_a_start_whose_stdout_and_stderr_are_both_gone_still_stops_its_cluster(
        docker, runtime):
    """``test.sh``'s start inherits the wrapper's stderr, so a run piped into a pipeline that
    is then killed (``test.sh 2>&1 | tee log``) leaves the start with BOTH outputs gone (the
    coordinator's condition A on commit B3).  It still stops what carries its token, since
    it writes nothing on stderr before or during that stop, and only then fails to say so:
    the report raises in turn, and CPython, unable to flush stderr as it exits, exits 120
    (measured 2026-10-19), a status ``test.sh`` reads as the failed start it is."""
    stub = docker()
    out, err = _unwritable("a pipe nobody reads"), _unwritable("a pipe nobody reads")
    try:
        started = subprocess.run(_START, stdout=out, stderr=err, check=False, timeout=30)
    finally:
        os.close(out)
        os.close(err)
    assert started.returncode == 120
    assert stub.calls()[-2:] == [f"rm -fv {_MADE}", _LISTED]
    assert not stub.made.exists()
    assert not (runtime / _DIRECTORY).exists()


def test_a_term_inside_a_failed_handover_s_stop_lets_the_stop_finish(docker, runtime):
    """A TERM that lands while a start that could not hand its cluster over is stopping it
    (its slow ``rm``) is HELD like any other: the stop finishes, lists again (the call that
    names a survivor) and removes the socket directory, and the start then ends BY the TERM
    with nothing left and nothing said.  The handover is the start's last held step, so its
    failure takes the start's one stop (the ruling on K1a commit B, item 6: the caller's
    signals are held until the handover or a finished stop).  A stop run once the hold had
    ended died at the TERM with its ``rm`` in flight, and left the socket directory."""
    stub = docker(_Behaviour(slow="rm"))
    out = _unwritable("a full disk")
    try:
        with subprocess.Popen(_START, stdout=out, stderr=subprocess.PIPE, text=True,
                              start_new_session=True) as start:
            _wait_for(stub, "rm")
            start.send_signal(signal.SIGTERM)
            _, err = _ended(start, runtime)
    finally:
        os.close(out)
    assert (start.returncode, err) == (-signal.SIGTERM, "")
    calls = stub.calls()
    assert calls[calls.index(f"rm -fv {_MADE}") + 1:] == [_LISTED], calls
    assert not stub.made.exists()
    assert not (runtime / _DIRECTORY).exists()


def test_stop_names_a_container_that_survived_and_exits_1(docker, runtime, capsys):
    """The planted leftover: docker still lists the token's container after its removal, so
    ``stop`` names it and how to remove it, and exits 1; it never reports clean."""
    docker(_Behaviour(survives=True)).hold(_MADE, _NAME, _LABEL)
    assert private_cluster.main(["stop", _TOKEN]) == 1
    assert capsys.readouterr().err == f"[private_cluster] WARNING: {_CONTAINER_LEFT}\n"
    assert not (runtime / _DIRECTORY).exists()


@pytest.mark.parametrize(("behaviour", "said"), [
    (_Behaviour(unreachable=True), f"docker could not be asked which containers carry "
                                   f"{_TOKEN}: Cannot connect to the Docker daemon"),
    (_Behaviour(slow="rm"), "docker rm did not answer within 1s; the daemon may be wedged"),
], ids=["unreachable", "wedged"])
def test_stop_names_a_daemon_it_cannot_ask_never_reporting_clean(docker, monkeypatch,
                                                                behaviour, said):
    """A daemon that cannot say which containers carry the token, or that does not answer
    within the per-command cap (here 1 s), is named, never read as a clean teardown."""
    docker(behaviour).hold(_MADE, _NAME, _LABEL)
    monkeypatch.setattr(private_cluster, "_DOCKER_TIMEOUT_SECONDS", 1)
    assert private_cluster.stop(_TOKEN) == [
        f"the container of cluster {_TOKEN} may have survived teardown: {said}"]


def test_stop_names_a_docker_it_cannot_run_never_a_traceback(tmp_path, monkeypatch):
    """A ``docker`` on ``PATH`` that cannot be executed is a named error, not an exception
    out of ``stop``: ``PATH`` holds ONLY that one, so the real docker is never reached."""
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "docker").write_text("#!/bin/sh\n", encoding="utf-8")
    (binaries / "docker").chmod(0o644)
    monkeypatch.setenv("PATH", str(binaries))
    assert private_cluster.stop(_TOKEN) == [
        f"the container of cluster {_TOKEN} may have survived teardown: docker cannot be "
        "run: [Errno 13] Permission denied: 'docker'"]


def test_stop_names_a_socket_directory_that_survived(docker, runtime, capsys):
    """A socket directory that cannot be removed (here a file in it this user cannot
    unlink) is named, and ``stop`` exits 1."""
    docker()
    held = runtime / _DIRECTORY
    held.mkdir()
    (held / "left-behind").write_text("", encoding="utf-8")
    held.chmod(0o500)
    try:
        assert private_cluster.main(["stop", _TOKEN]) == 1
        assert capsys.readouterr().err == f"[private_cluster] WARNING: {held} survived teardown.\n"
    finally:
        held.chmod(0o700)


def test_stop_of_a_cluster_that_never_started_is_clean(docker, runtime, capsys):
    """What lets a caller's traps call ``stop`` whenever they fire: nothing to remove is
    nothing survived, exit 0, said by nobody."""
    docker()
    assert private_cluster.main(["stop", _TOKEN]) == 0
    assert capsys.readouterr().err == ""
    assert not (runtime / _DIRECTORY).exists()


def test_a_socket_path_binds_at_107_bytes_and_not_at_108():
    """A socket binds at a path of exactly 107 bytes and not at 108 (CPython refuses the
    108th itself, "AF_UNIX path too long": ``sun_path`` holds 108 with its terminator).
    Postgres appends ``/.s.PGSQL.5432`` (14 bytes), so a socket directory can take 93; the
    two cases after ``_padded`` hold the module to that boundary."""
    base = Path(tempfile.mkdtemp(prefix="skrt", dir="/tmp"))
    try:
        for size, binds in ((107, True), (108, False)):
            path = base / ("s" * (size - len(f"{base}/".encode())))
            assert len(str(path).encode()) == size
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.bind(str(path))
                bound = True
            except OSError:
                bound = False
            finally:
                listener.close()
            assert bound is binds, f"a {size}-byte socket path bound: {bound}"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _padded(runtime: Path, size: int, filler: str) -> Path:
    """An ``XDG_RUNTIME_DIR`` under ``runtime`` whose socket directory for :data:`_TOKEN` is
    ``size`` BYTES: ``filler`` as often as it fits, then ``d`` to the exact count."""
    room = size - len(os.fsencode(runtime / _DIRECTORY)) - 1
    each = len(os.fsencode(filler))
    padding = filler * (room // each) + "d" * (room % each)
    assert len(os.fsencode(runtime / padding / _DIRECTORY)) == size
    return runtime / padding


def test_a_socket_directory_of_93_bytes_starts_a_cluster_whose_socket_binds_there(
        docker, runtime, monkeypatch):
    """The budget's top: a socket directory of 93 bytes starts a cluster, and its socket
    binds there at 107 bytes, the kernel's limit."""
    docker()
    padded = _padded(runtime, 93, "d")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(padded))
    assert private_cluster.start(_NAME, _TOKEN, _IMAGE).socket_dir == padded / _DIRECTORY
    assert (padded / _DIRECTORY / ".s.PGSQL.5432").is_socket()


def test_a_socket_directory_that_is_not_utf8_is_refused_before_docker_is_asked(
        docker, runtime, monkeypatch):
    """A socket directory holding bytes that are not UTF-8 (``XDG_RUNTIME_DIR`` may hold
    them) is refused at the door (exit 2) before docker is asked anything, though at 93
    bytes it fits the length budget: a real daemon is handed such a byte as U+FFFD and
    mounts another directory, so a real start there waited out its whole readiness cap (the
    reviews of 668b53c99 and bb2f1492a measured it).  The refusal quotes each such byte
    escaped.

    Everything compared is ASCII (``ascii``): a failure message holding such a path cannot
    be sent between xdist's processes, and would end the whole session instead of failing
    this case.
    """
    stub = docker()
    padded = _padded(runtime, 93, "\udcff")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(padded))
    try:
        started = private_cluster.start(_NAME, _TOKEN, _IMAGE)
    except private_cluster.ClusterError as failed:
        said = (type(failed).__name__, ascii(str(failed)))
    else:
        said = ("started", ascii(str(started.socket_dir)))
    shown = os.fsencode(padded / _DIRECTORY).decode("utf-8", "backslashreplace")
    assert said == ("ClusterError", ascii(
        f"socket directory {shown} is not UTF-8, so docker would mount another directory in "
        "its place.  Set XDG_RUNTIME_DIR to a path that is."))
    assert stub.calls() == [], ascii(stub.calls())


@pytest.mark.parametrize("filler", [
    "d",
    # Two-byte characters: counted in characters, the directory would fit.
    "é",
], ids=["94 bytes", "few characters, many bytes"])
def test_a_socket_directory_of_94_bytes_is_refused_before_docker_is_asked(
        docker, runtime, monkeypatch, filler):
    """The guard counts BYTES as the kernel does: a socket directory of 94 bytes is refused
    naming the 107-byte limit, before docker is asked anything, and so is one of 94 bytes in
    two-byte characters, whose characters would fit."""
    stub = docker()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(_padded(runtime, 94, filler)))
    with pytest.raises(private_cluster.ClusterError, match="cannot exceed 107 bytes"):
        private_cluster.start(_NAME, _TOKEN, _IMAGE)
    assert stub.calls() == []


def test_the_budget_counts_what_would_fit_in_characters_as_too_long(runtime):
    """The control for the two-byte case above: its characters would fit the budget."""
    directory = _padded(runtime, 94, "é") / _DIRECTORY
    assert len(str(directory)) <= 93 < len(os.fsencode(directory))


@pytest.mark.parametrize("given", ["soon", "-1", "nan", "inf"])
def test_a_readiness_cap_that_is_not_a_finite_number_of_seconds_is_refused(
        docker, runtime, monkeypatch, capsys, given):
    """``READINESS_TIMEOUT_SECONDS`` set to anything but a finite, non-negative number of
    seconds exits 2 naming it, before anything is asked or started (``nan`` and ``inf``
    would make the readiness wait endless: the review's LOW-4)."""
    stub = docker()
    monkeypatch.setenv("READINESS_TIMEOUT_SECONDS", given)
    assert private_cluster.main(["start", _NAME, _TOKEN, _IMAGE]) == 2
    assert f"READINESS_TIMEOUT_SECONDS={given!r}" in capsys.readouterr().err
    assert stub.calls() == []
    assert not (runtime / _DIRECTORY).exists()


def test_an_unset_readiness_cap_is_15_seconds(docker, monkeypatch, capsys):
    """``READINESS_TIMEOUT_SECONDS`` unset caps the wait at its default, 15 seconds: a
    default of 0 passed every case before this one (the review of 668b53c99's MX8), since
    this file's own fixture sets the cap.  A case cannot wait 15 seconds out, so the
    module's clock moves 5 seconds each time it is read and its sleep returns at once; the
    start gives up only once that clock reaches 15, naming the cap."""
    docker(_Behaviour(ready=False))
    monkeypatch.delenv("READINESS_TIMEOUT_SECONDS")
    readings = itertools.count(0, 5)
    monkeypatch.setattr(private_cluster, "time", SimpleNamespace(
        monotonic=lambda: float(next(readings)), sleep=lambda _seconds: None))
    assert private_cluster.main(["start", _NAME, _TOKEN, _IMAGE]) == 2
    assert capsys.readouterr().err == (
        f"[private_cluster] {_NAME} did not accept connections within 15s\n")
