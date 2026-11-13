"""The rate limiter's real store (ruling R-BAL298): the pinned Valkey image, started as the
compose files start it, under the committed ACL, driven by the app's own limiter.

The suite runs the limiter on ``memory://`` (TestConfig), which refuses no command, so a grant
missing from ``deploy/valkey/users.acl`` would pass it and fail in production, where the
limiter is fail-closed (``RATELIMIT_SWALLOW_ERRORS`` off, no in-memory fallback) and every
rate-limited request would fail.  These tests run the store as production does:

* The container is ``docker-compose.yml``'s ``valkey`` service as that file declares it
  (:func:`_service_args`): its image by tag and digest, its command (``sh
  /etc/valkey/start.sh``), its user, its network (none), its read-only root and tmpfs mounts,
  its dropped capabilities, its security options and its bind of ``deploy/valkey/`` at
  ``/etc/valkey``, and the test waits on its healthcheck, run in the container as docker
  runs it.  Not its resource caps (a rootless daemon need not delegate the cgroup controllers
  they use), and in place of its socket volume a folder of the test's own
  (:func:`_sockets_folder`), where the test, on the host, connects to the socket at the path
  the app's store address names (ruling plan#2612), the address the app's configuration
  holds in code (ruling plan#2633): the server listens on no network port.
  The volume itself, as declared, is graded on its own: it admits the app's user and no
  other.
* The app's own limiter (Flask-Limiter on limits' moving-window strategy, BaseConfig's
  settings) drives ``/login``'s ``5 per 15 minutes`` logged in by the app's own storage
  options, as the ``shekel`` user with the server's password: each request's
  acquire (``acquire_moving_window.lua``) and the window read behind its X-RateLimit-*
  headers (``moving_window.lua``), each run by EVALSHA on a server that holds no script yet,
  so valkey-py loads each by SCRIPT LOAD first.
* The ``shekel`` user's grants are graded both ways.  Each is one the limiter needs: the
  limiter passes under the committed rules, and fails under them with any one grant taken
  out, each read from the committed file.  And the user holds nothing more.  Its commands:
  the server is asked (``ACL DRYRUN``) of every command and subcommand it knows, and those
  the user may run beyond what no grant can refuse are the limiter's needs, read off the
  server: each command ``MONITOR`` showed it send in a ``/login`` run as a user granted
  everything whose withholding fails or changes that run.  Its keys: the server's reading of
  its rules
  (``ACL GETUSER``) holds no selector and no key pattern outside the prefix limits writes
  every key under, ``LIMITS:``.  So a command grant widened in place (``+@list`` for the
  list commands), the rules turned to a deny-list (``+@all`` less what the limiter does not
  send) and a key pattern widened in place (``~*:*``) each fail.

``docker``-marked: ``scripts/test.sh`` deselects them unless asked
(``PYTEST_MARKER_EXPR=docker``), CI runs them, and this package's conftest refuses the
system daemon outside CI.  A container that does not start FAILS the test rather than
skipping it: a skip would pass the gate these tests exist to be.
"""
from __future__ import annotations

import ast
import contextlib
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from unittest import mock

import pytest
import valkey
from valkey.exceptions import AuthenticationError, NoPermissionError
from werkzeug.test import TestResponse

from app import create_app
from app.config import DevContainerConfig, ProdConfig
from app.extensions import db
from app.rate_limits import limiter
from tests._seed_owner_fixtures import SEED_USER_EMAIL
from tests.test_deploy._compose import REPO_ROOT, service, volume

pytestmark = pytest.mark.docker

#: The valkey service as ``docker-compose.yml`` declares it.
_VALKEY = service("valkey")
#: The app service as ``docker-compose.yml`` declares it.
_APP = service("app")
#: The valkey service's two mounts: ``deploy/valkey/``, bound read-only at ``/etc/valkey``,
#: and the volume it makes its socket in.
(_BIND,) = [mount for mount in _VALKEY["volumes"] if mount["type"] == "bind"]
(_SOCKETS,) = [mount for mount in _VALKEY["volumes"] if mount["type"] == "volume"]
#: The app's mount of that volume.
(_APP_SOCKETS,) = [mount for mount in _APP["volumes"]
                   if isinstance(mount, dict) and mount["source"] == _SOCKETS["source"]]
#: That volume, as the file's top-level ``volumes`` declares it.
_SOCKET_VOLUME = volume(_SOCKETS["source"])
#: The rules and start script the service mounts, as committed.
_RULES = (REPO_ROOT / _BIND["source"]).resolve()
#: How a store address names a socket file: limits' scheme for the valkey client over one.
_SOCKET_SCHEME = "valkey+unix://"


def _app_socket() -> PurePosixPath:
    """Where the app connects to the store: the socket production's configuration names, in
    the container.

    Raises:
        AssertionError: when that configuration names its store by other than a socket file.
    """
    address = ProdConfig.RATELIMIT_STORAGE_URI
    assert address.startswith(_SOCKET_SCHEME), f"the app names no socket: {address}"
    return PurePosixPath(address.removeprefix(_SOCKET_SCHEME))


#: The app's socket, in the container, and its path inside the socket volume.
_SOCKET = _app_socket()
_SOCKET_NAME = _SOCKET.relative_to(_SOCKETS["target"])
#: The service's healthcheck, a shell command docker runs in the container.
_HEALTHCHECK_FORM, _HEALTHCHECK = _VALKEY["healthcheck"]["test"]
#: A folder the test binds where the service mounts its socket's: anyone may write in it, so
#: the server's user, no user on the host, may make its socket there, and its setgid bit
#: hands the folder's group, the test's own, to the socket, so the test's user connects
#: through the group's permission (``start.sh``'s ``--unixsocketperm``), as the app connects
#: through the volume's group in production.  Its parent is the test's alone.
_SOCKETS_MODE = 0o2777
#: Where ``start.sh`` reads a mounted password file from.
_SECRET = "/run/secrets/valkey_password"
#: The app's user in ``users.acl``, as whom the app logs in.
_APP_USER = "shekel"
#: How ``users.acl`` starts the ``shekel`` user's one line.
_SHEKEL = f"user {_APP_USER} "
#: How long one docker command may take, a pull of the pinned image included.
_DOCKER_TIMEOUT_S = 40
#: How long a started server has to answer its healthcheck user's PING.
_READY_S = 10.0
#: ``/login``'s limit, ``5 per 15 minutes``: five POSTs pass and the sixth is refused.
_LOGIN_LIMIT = 5


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    """Run ``docker`` with ``args``, its output captured; its exit status is the caller's."""
    return subprocess.run(["docker", *args], capture_output=True, text=True,
                          timeout=_DOCKER_TIMEOUT_S, check=False)


def _service_args(name: str, rules: Path, sockets: str, user: str = _VALKEY["user"]
                  ) -> list[str]:
    """``docker run``'s arguments for the valkey service, named ``name``, with ``rules``
    mounted where the service mounts ``deploy/valkey/`` and ``sockets`` (a mount's type and
    source) where it mounts its socket's volume: each option :data:`_VALKEY` declares that
    shapes how the server starts, as whom (``user``, the declared one unless given) and where
    it may be reached, then its image and command."""
    readonly = ",readonly" if _BIND["read_only"] else ""
    args = ["--name", name, "--user", user, "--network", _VALKEY["network_mode"],
            "--mount", f"type=bind,source={rules},target={_BIND['target']}{readonly}",
            "--mount", f"{sockets},target={_SOCKETS['target']}"
                       f"{',readonly' if _SOCKETS.get('read_only') else ''}"]
    for path in _VALKEY["tmpfs"]:
        args += ["--tmpfs", path]
    return [*args, *_hardening(_VALKEY), _VALKEY["image"], *_VALKEY["command"]]


def _hardening(declared: dict) -> list[str]:
    """``docker run``'s arguments for what the service ``declared`` locks down: its read-only
    root, its dropped capabilities and its security options."""
    args = ["--read-only"] if declared["read_only"] else []
    for capability in declared["cap_drop"]:
        args += ["--cap-drop", capability]
    for option in declared["security_opt"]:
        args += ["--security-opt", option]
    return args


@dataclass(frozen=True)
class _Store:
    """A running server: its container's ``name``, the socket the test reaches it at on the
    host, and the password of its ``shekel`` user."""

    name: str
    socket: Path
    password: str

    @property
    def login(self) -> dict[str, str]:
        """The app's own login to this server: the storage options its configurations beside
        Valkey build (``app.config._ValkeyStore``, shared by DevContainerConfig and
        ProdConfig), given this server's ``shekel`` password where the entrypoint puts it."""
        with mock.patch.dict(os.environ, {"VALKEY_PASSWORD": self.password}):
            return DevContainerConfig().RATELIMIT_STORAGE_OPTIONS

    @staticmethod
    def login_of(username: str, password: str) -> dict[str, str]:
        """The storage options that log in as ``username`` by ``password``."""
        return {"username": username, "password": password}

    def client(self, username: str | None = None, password: str | None = None
               ) -> valkey.Valkey:
        """A client of this server, logged in as ``username`` (none: not logged in)."""
        return valkey.Valkey(unix_socket_path=str(self.socket), username=username,
                             password=password, socket_timeout=_READY_S)


def _logs(name: str) -> str:
    """What the container ``name`` has written, its output then its errors."""
    logs = _docker("logs", name)
    return logs.stdout + logs.stderr


def _started(name: str, args: list[str]) -> None:
    """Run the valkey service as ``args`` give it, named ``name``, and wait until its
    healthcheck passes, run in the container as docker runs it (``CMD-SHELL``: by the
    container's shell).

    Raises:
        AssertionError: when the container does not start, or its healthcheck does not pass
            within :data:`_READY_S`, naming what docker or the server said.
    """
    assert _HEALTHCHECK_FORM == "CMD-SHELL", f"a healthcheck run as {_HEALTHCHECK_FORM}"
    started = _docker("run", "--detach", *args)
    assert started.returncode == 0, f"the valkey service did not start: {started.stderr}"
    deadline = time.monotonic() + _READY_S
    while _docker("exec", name, "sh", "-c", _HEALTHCHECK).returncode != 0:
        assert time.monotonic() <= deadline, (
            f"the valkey service's healthcheck did not pass within {_READY_S} s:\n"
            f"{_logs(name)}")
        time.sleep(0.1)


def _sockets_folder(parent: Path) -> Path:
    """A folder in ``parent`` for the server's socket, made :data:`_SOCKETS_MODE`."""
    folder = parent / "sockets"
    folder.mkdir()
    folder.chmod(_SOCKETS_MODE)
    return folder


@contextlib.contextmanager
def _served(password: str, given: list[str], rules: Path = _RULES) -> Iterator[_Store]:
    """Run the valkey service with ``rules`` mounted and a folder of the test's own where it
    mounts its socket's volume, handed its ``shekel`` password ``password`` by the options
    ``given``, until its healthcheck passes; yield it, then remove its container and the
    folder whatever happened.

    Raises:
        AssertionError: when the container does not start, or its healthcheck does not pass
            within :data:`_READY_S`.
    """
    name = f"shekel-test-valkey-{secrets.token_hex(4)}"
    with tempfile.TemporaryDirectory() as parent:
        sockets = _sockets_folder(Path(parent))
        try:
            _started(name, [*given, *_service_args(name, rules, f"type=bind,source={sockets}")])
            yield _Store(name, sockets / _SOCKET_NAME, password)
        finally:
            _docker("rm", "--force", name)


@pytest.fixture(name="store")
def _store() -> Iterator[_Store]:
    """A fresh server, handed its ``shekel`` password in ``VALKEY_PASSWORD`` as the base and
    dev compose files hand it."""
    password = secrets.token_hex(16)
    with _served(password, ["--env", f"VALKEY_PASSWORD={password}"]) as store:
        yield store


def _login_answers(socket: Path, login: dict[str, str]) -> list[TestResponse]:
    """``/login``'s limit and one more POSTs, through the app's own limiter on the store at
    ``socket``, logged in by the storage options ``login``, each answer in turn.  The limiter
    is fail-closed, so a command the store refuses raises out of the request (a
    :class:`valkey.exceptions.ValkeyError`)."""
    rate_app = create_app("testing")
    rate_app.config.update(RATELIMIT_ENABLED=True,
                           RATELIMIT_STORAGE_URI=f"{_SOCKET_SCHEME}{socket}",
                           RATELIMIT_STORAGE_OPTIONS=login)
    limiter.enabled = True
    limiter.init_app(rate_app)
    try:
        client = rate_app.test_client()
        with rate_app.app_context():
            return [client.post("/login", data={"email": SEED_USER_EMAIL,
                                                "password": "not-the-password"})
                    for _ in range(_LOGIN_LIMIT + 1)]
    finally:
        # Never limiter.reset(): it clears the store with KEYS and DEL, which the ACL refuses.
        with rate_app.app_context():
            db.engine.dispose()
        limiter.enabled = False


#: How ``/proc/net/tcp`` and ``tcp6`` mark a socket that listens (``TCP_LISTEN``), in their
#: fourth column.
_LISTENING = "0A"


def test_the_server_listens_on_no_network_port(store):
    """The server is reached only through its socket: in its container, which joins no
    network, ``/proc/net/tcp`` and ``tcp6`` hold no listening socket, where without
    ``start.sh``'s ``--port 0`` it would listen on the container's own loopback."""
    tables = _docker("exec", store.name, "cat", "/proc/net/tcp", "/proc/net/tcp6")
    assert tables.returncode == 0, tables.stderr
    rows = [line.split() for line in tables.stdout.splitlines()]
    assert [row[1] for row in rows if row[3] == _LISTENING] == []


@pytest.mark.usefixtures("seed_user")
def test_a_limited_route_runs_on_the_store_as_the_app_s_user(store):
    """``/login``'s five POSTs pass on the store, each answered with the window it read
    (X-RateLimit-Remaining 4 down to 0), and the sixth is refused, with when to retry: the
    acquire and the window read each ran, by EVALSHA after SCRIPT LOAD on the fresh server,
    as the ``shekel`` user.  A grant missing from the ACL fails a request instead."""
    answers = _login_answers(store.socket, store.login)
    assert [answer.status_code for answer in answers] == [200] * _LOGIN_LIMIT + [429]
    assert [answer.headers.get("X-RateLimit-Remaining") for answer in answers] == [
        "4", "3", "2", "1", "0", "0"]
    assert int(answers[-1].headers["Retry-After"]) > 0


#: A key outside ``LIMITS:*``, the prefix limits writes every key under.
_ELSEWHERE = "elsewhere"
#: What the ``shekel`` user may not run, each as sent: what ``users.acl`` names as not
#: granted (PING, DEL, TTL, KEYS), two no limiter sends (FLUSHALL, CONFIG), and a granted
#: command on a key outside ``LIMITS:*``.
_OUTSIDE = (("PING",), ("DEL", "LIMITS:a"), ("TTL", "LIMITS:a"), ("KEYS", "LIMITS:*"),
            ("FLUSHALL",), ("CONFIG", "GET", "maxmemory"), ("LPUSH", _ELSEWHERE, "1"))


def test_every_command_outside_the_app_s_grant_is_refused(store):
    """The ``shekel`` user is refused with NOPERM each command its grant leaves out and a
    granted one on a key outside its pattern; a client not logged in is refused everything,
    since the rules switch the default user off."""
    shekel = store.client(username=_APP_USER, password=store.password)
    refused: dict[str, bool] = {}
    for command in _OUTSIDE:
        try:
            shekel.execute_command(*command)
        except NoPermissionError:
            refused[" ".join(command)] = True
        else:
            refused[" ".join(command)] = False
    assert refused == {" ".join(command): True for command in _OUTSIDE}
    with pytest.raises(AuthenticationError):
        store.client().execute_command("LINDEX", "LIMITS:a", "0")


def test_start_sh_reads_production_s_password_file_over_a_blank_variable(tmp_path):
    """Production's way (``deploy/docker-compose.prod.yml``): the password in a file mounted
    at ``/run/secrets/valkey_password`` with the trailing newline ``echo`` leaves, and
    ``VALKEY_PASSWORD`` blanked.  The ``shekel`` user logs in with the password alone and runs
    a granted command.  The file is 0644 here, where production's is 0640 read through its
    group: a rootless daemon remaps uids, so the group read is not what this measures."""
    password = secrets.token_hex(16)
    secret = tmp_path / "valkey_password"
    secret.write_text(f"{password}\n", encoding="utf-8")
    secret.chmod(0o644)
    mounted = f"type=bind,source={secret},target={_SECRET},readonly"
    with _served(password, ["--env", "VALKEY_PASSWORD=", "--mount", mounted]) as store:
        shekel = store.client(username=_APP_USER, password=password)
        assert shekel.execute_command("LINDEX", "LIMITS:a", "0") is None


def _shekel_tokens() -> list[str]:
    """What the committed ``users.acl`` gives the ``shekel`` user, each token of its one line
    after the user's name.

    Raises:
        AssertionError: when the file names the user on other than one line.
    """
    lines = [line for line in (_RULES / "users.acl").read_text(encoding="utf-8").splitlines()
             if line.startswith(_SHEKEL)]
    assert len(lines) == 1, "users.acl names the shekel user on one line"
    return lines[0].removeprefix(_SHEKEL).split()


def _rules_where_shekel(tmp_path: Path, tokens: list[str] | None,
                        also: tuple[str, ...] = ()) -> Path:
    """A copy of the committed rules and start script whose ``users.acl`` gives the ``shekel``
    user ``tokens`` after its name, or names no ``shekel`` user where ``tokens`` is None, its
    one line dropped; then each line of ``also``."""
    rules = tmp_path / "rules"
    shutil.copytree(_RULES, rules)
    acl = rules / "users.acl"
    acl.write_text("".join(
        [line if not line.startswith(_SHEKEL)
         else "" if tokens is None else f"{_SHEKEL}{' '.join(tokens)}\n"
         for line in acl.read_text(encoding="utf-8").splitlines(keepends=True)]
        + [f"{line}\n" for line in also]),
        encoding="utf-8")
    rules.chmod(0o755)
    return rules


#: How a token of an ACL line that restricts rather than grants starts: a command or category
#: taken away, a reset, a password removed by itself or by its hash.
_RESTRICTING = ("-", "reset", "<", "!")
#: The tokens Valkey reads as restrictions by name: the user disabled, no commands, no
#: selectors.
_RESTRICTIONS = frozenset({"off", "nocommands", "clearselectors"})
#: Each token of the ``shekel`` user's line that grants something: a command, a key pattern,
#: ``on`` itself; every token but a restriction (:data:`_RESTRICTING`,
#: :data:`_RESTRICTIONS`), read from the committed file.
_GRANTS = [token for token in _shekel_tokens()
           if not token.startswith(_RESTRICTING) and token not in _RESTRICTIONS]
#: What the store says refusing the ``shekel`` user each grant, in any case (valkey-py drops
#: the NOPERM prefix: "User shekel has no permissions to run the 'evalsha' command"): no
#: permission for a command or a key, an ACL failure for a command a script calls, and a
#: disabled user for ``on``.
_REFUSED = r"(?i)no permissions|ACL failure in script|user is disabled"


@pytest.mark.usefixtures("seed_user")
def test_the_rules_copied_whole_serve_the_limiter(tmp_path):
    """The control for the grants test below: the committed rules copied by the same helper,
    nothing taken out, and the limiter passes its five and refuses the sixth, so a refusal
    there is the grant's, never the copy's."""
    password = secrets.token_hex(16)
    rules = _rules_where_shekel(tmp_path, _shekel_tokens())
    with _served(password, ["--env", f"VALKEY_PASSWORD={password}"], rules) as store:
        answers = _login_answers(store.socket, store.login)
    assert [answer.status_code for answer in answers] == [200] * _LOGIN_LIMIT + [429]


@pytest.mark.usefixtures("seed_user")
@pytest.mark.parametrize("grant", _GRANTS)
def test_each_grant_of_the_app_s_user_is_one_the_limiter_needs(tmp_path, grant):
    """The ``shekel`` user's line with one grant taken out, and the store refuses the
    limiter's requests for that grant (:data:`_REFUSED`): so each token the user holds is one
    the limiter needs, where :func:`test_a_limited_route_runs_on_the_store_as_the_app_s_user`
    shows it holds all it does.  The grants are read from the committed file, so one added
    there (``+get``, ``allkeys``, ``&*``) is graded with no edit here.  Graded both ways: a
    command grant widened in place (``+script`` for ``+script|load``) still holds a token the
    limiter needs, and :func:`test_the_app_s_user_may_run_only_what_the_limiter_needs` fails
    it."""
    password = secrets.token_hex(16)
    rules = _rules_where_shekel(tmp_path, [token for token in _shekel_tokens()
                                           if token != grant])
    with _served(password, ["--env", f"VALKEY_PASSWORD={password}"], rules) as store:
        with pytest.raises(valkey.exceptions.ValkeyError, match=_REFUSED):
            _login_answers(store.socket, store.login)


#: The census's test-only users, none in the committed file.  ``census`` is granted every
#: command, key and channel (:data:`_EVERYTHING`): the limiter runs as it, so that what it
#: sends reaches the server whether or not the committed rules would refuse it, and the test
#: watches and asks the server as it.
_CENSUS = "census"
#: ``nobody`` is switched off and granted nothing, so what it may run no grant can refuse.
_NOBODY = "nobody"
#: ``withheld`` is granted everything but one command, each the limiter sent in turn.
_WITHHELD = "withheld"
#: The password a ``nopass`` user logs in with: any, so none.
_NOPASS = ""
#: An ACL rule's every command, key and channel.
_EVERYTHING = ("allkeys", "allchannels", "+@all")
#: The lines the census adds to its copy of the rules.
_CENSUS_USERS = (f"user {_CENSUS} on nopass {' '.join(_EVERYTHING)}",
                 f"user {_NOBODY} off resetchannels -@all")
#: How ``ACL DRYRUN`` refuses a user a command or a key.
_NO_PERMISSION = re.compile(rb"User \S+ has no permissions to ")
#: How it refuses a user a key, naming the ``key``.
_NO_KEY = re.compile(_NO_PERMISSION.pattern + rb"access the '(?P<key>[^']*)' key")
#: The fields of a command in ``COMMAND``'s reply, a subcommand's as its command's: its name,
#: arity, key specifications and subcommands.
_NAME, _ARITY, _KEY_SPECS, _SUBCOMMANDS = 0, 1, 8, 9
#: A key pattern as ``ACL GETUSER`` shows one: ``~`` and its glob, ``%R~`` for one the user
#: may only read, ``%W~`` for one it may only write.
_KEY_PATTERN = re.compile(r"(?:%R?W?)?~(?P<glob>.*)")
#: What a glob reads as other than itself.
_GLOB_CHARACTERS = frozenset("*?[]\\")
#: A line ``MONITOR`` writes: when, the database and the client that sent the command in
#: brackets (``lua`` for one a script called), then each argument.
_MONITORED = re.compile(rb"\d+\.\d+ \[\d+ [^\]]+\] (?P<arguments>.*)")
#: One argument of such a line: in double quotes, a quote, backslash or unprintable byte
#: inside escaped as a Python bytes literal escapes it.
_ARGUMENT = re.compile(rb'"(?:[^"\\]|\\.)*"')


@dataclass(frozen=True)
class _Command:
    """A command the server knows, as ``ACL DRYRUN`` asks of one: its ``name`` (a
    subcommand's joined to its command's, ``script|load``), the ``words`` that start it, its
    ``arity`` (the arguments it takes, its words included; negative: at least that many) and
    whether it is ``keyed``, declaring a key."""

    name: str
    words: tuple[str, ...]
    arity: int
    keyed: bool

    @classmethod
    def of(cls, entry: list) -> _Command:
        """The command one entry of ``COMMAND``'s reply describes."""
        name = entry[_NAME].decode()
        return cls(name, tuple(name.split("|")), entry[_ARITY], bool(entry[_KEY_SPECS]))

    def asked(self, key: str) -> tuple[str, ...]:
        """The command with ``key`` as each argument its arity needs past its words."""
        return (*self.words, *[key] * (abs(self.arity) - len(self.words)))


def _commands(store: _Store) -> list[_Command]:
    """Every command ``store`` knows (``COMMAND``), as each is run: each subcommand, and each
    command that has none or runs with no argument (``COMMAND`` itself).  The reply is read
    raw, a command and its subcommands alike, where valkey-py's own reading of it leaves the
    subcommands raw."""
    client = store.client(username=_CENSUS, password=_NOPASS)
    client.set_response_callback("COMMAND", lambda response, **_: response)
    commands = []
    for entry in client.execute_command("COMMAND"):
        if not entry[_SUBCOMMANDS] or abs(entry[_ARITY]) == 1:
            commands.append(_Command.of(entry))
        commands += [_Command.of(subcommand) for subcommand in entry[_SUBCOMMANDS]]
    return commands


def _may_run(client: valkey.Valkey, user: str, commands: list[_Command], key: str) -> set[str]:
    """The names of ``commands`` the server says ``user`` may run, each asked by ``ACL
    DRYRUN`` with ``key`` as each argument it takes.

    Raises:
        AssertionError: when the server answers any of them with other than ``OK`` or a
            refusal (an arity it does not take, a command it does not know), naming each.
    """
    pipe = client.pipeline(transaction=False)
    for command in commands:
        pipe.acl_dryrun(user, *command.asked(key))
    answers = dict(zip((command.name for command in commands),
                       pipe.execute(raise_on_error=False), strict=True))
    unanswered = {name: answer for name, answer in answers.items() if answer != b"OK" and not (
        isinstance(answer, bytes) and _NO_PERMISSION.match(answer))}
    assert not unanswered, f"ACL DRYRUN gave {user} no verdict on: {unanswered}"
    return {name for name, answer in answers.items() if answer == b"OK"}


def _argv(line: bytes) -> list[bytes]:
    """The command a ``MONITOR`` line shows, each argument as it was sent.

    Raises:
        AssertionError: when the line is not one ``MONITOR`` writes, or holds what is not an
            argument in quotes.
    """
    shown = _MONITORED.fullmatch(line)
    assert shown, f"not a MONITOR line: {line!r}"
    arguments = _ARGUMENT.findall(shown["arguments"])
    assert b" ".join(arguments) == shown["arguments"], f"a MONITOR line read in part: {line!r}"
    return [ast.literal_eval(f"b{argument.decode('ascii')}") for argument in arguments]


def _first_sent(client: valkey.Valkey, commands: list[_Command], seen: list[list[bytes]]
                ) -> dict[str, tuple[list[bytes], set[bytes]]]:
    """Each command of ``commands`` that ``seen`` runs, by name, with the arguments it was
    first sent with and the keys the server reads in them (``COMMAND GETKEYS``).  A command
    ``argv`` runs is the subcommand its first two words name, else the command its first
    does."""
    by_words = {command.words: command for command in commands}
    sent: dict[str, tuple[list[bytes], set[bytes]]] = {}
    for argv in seen:
        words = tuple(word.decode().lower() for word in argv[:2])
        command = by_words.get(words) or by_words[words[:1]]
        if command.name not in sent:
            sent[command.name] = (argv, {key.encode() for key in client.command_getkeys(*argv)}
                                  if command.keyed else set())
    return sent


def _login_watched(store: _Store) -> tuple[list[TestResponse], list[list[bytes]]]:
    """``/login``'s run as :data:`_CENSUS`, and each command ``MONITOR`` showed during it,
    the commands a script called included, its arguments as sent.  The watch ends at a marker
    sent on a connection made before it started, so the marker is that connection's one line
    in it."""
    marker = secrets.token_hex(8).encode()
    sentinel = store.client(username=_CENSUS, password=_NOPASS)
    sentinel.ping()
    with store.client(username=_CENSUS, password=_NOPASS).monitor() as monitor:
        answers = _login_answers(store.socket, _Store.login_of(_CENSUS, _NOPASS))
        sentinel.echo(marker)
        seen = []
        while (argv := _argv(monitor.connection.read_response())) != [b"ECHO", marker]:
            seen.append(argv)
    return answers, seen


def _outcome(answers: list[TestResponse]) -> list[tuple[int, str | None]]:
    """What each answer of a run said: its status and the window it read as left."""
    return [(answer.status_code, answer.headers.get("X-RateLimit-Remaining"))
            for answer in answers]


def _needed(store: _Store, sent: Iterable[str], outcome: list[tuple[int, str | None]]
            ) -> set[str]:
    """Each command of ``sent`` the limiter needs: withheld from :data:`_WITHHELD`, on a
    server holding no key and no script, as a fresh one holds, ``/login``'s run as that user
    is refused, or answers other than ``outcome``, the run's with nothing withheld.

    Raises:
        valkey.exceptions.ValkeyError: when a run fails other than by a refusal
            (:data:`_REFUSED`).
    """
    census = store.client(username=_CENSUS, password=_NOPASS)
    needed = set()
    for name in sorted(sent):
        census.execute_command("ACL", "SETUSER", _WITHHELD, "reset", "on", "nopass",
                               *_EVERYTHING, f"-{name}")
        census.flushall()
        census.script_flush()
        try:
            answers = _login_answers(store.socket, _Store.login_of(_WITHHELD, _NOPASS))
        except valkey.exceptions.ValkeyError as error:
            if not re.search(_REFUSED, str(error)):
                raise
            needed.add(name)
        else:
            if _outcome(answers) != outcome:
                needed.add(name)
    return needed


def _inside(pattern: str, prefix: str) -> bool:
    """Whether the key pattern ``pattern``, as ``ACL GETUSER`` shows one, takes in only keys
    that start ``prefix``, which holds no glob character: its glob starts with ``prefix`` as
    written.  One that spells the prefix otherwise (``[L]IMITS:*``) reads as outside."""
    assert not _GLOB_CHARACTERS & set(prefix), f"{prefix!r} reads as a glob"
    shown = _KEY_PATTERN.fullmatch(pattern)
    return bool(shown) and shown["glob"].startswith(prefix)


def _held_outside(client: valkey.Valkey, prefix: str) -> tuple[list, list[str]]:
    """What the app's user holds beyond keys that start ``prefix``, as the server reads its
    rules (``ACL GETUSER``): its selectors, and each key pattern not :func:`_inside` it."""
    held = client.acl_getuser(_APP_USER)
    return held["selectors"], [pattern for pattern in held["keys"]
                               if not _inside(pattern, prefix)]


def _aimed_elsewhere(client: valkey.Valkey, argv: list[bytes], keys: set[bytes]
                     ) -> tuple[bytes, bytes]:
    """What ``ACL DRYRUN`` answers the app's user of ``argv`` as sent, then of ``argv`` with
    :data:`_ELSEWHERE` for each of its arguments in ``keys``: the key it refuses, where it
    refuses one, else its answer."""
    as_sent = client.acl_dryrun(_APP_USER, *argv)
    aimed = client.acl_dryrun(_APP_USER, *[_ELSEWHERE.encode() if argument in keys
                                           else argument for argument in argv])
    refused = _NO_KEY.fullmatch(aimed)
    return as_sent, refused["key"] if refused else aimed


@pytest.mark.usefixtures("seed_user")
def test_the_app_s_user_may_run_only_what_the_limiter_needs(tmp_path):
    """The ``shekel`` user may run only what the limiter needs, on only limits' keys.  The
    committed rules are copied with :data:`_CENSUS_USERS` added.  ``/login``'s run as
    :data:`_CENSUS` passes its five and refuses the sixth, and what ``MONITOR`` showed during
    it is what the limiter sent, read there and never written here.  ``MONITOR`` leaves out
    admin commands (``CONFIG``): one the limiter sent and needed would read as granted beyond
    its needs, so the census would fail, not pass.  Of what it sent, the limiter needs each
    that :func:`_needed` finds; ``CLIENT SETINFO``, which valkey-py sends on each connection
    and whose refusal it ignores (``users.acl`` says why), is sent and not needed.

    Commands: the server is asked whether ``shekel`` may run each command and subcommand it
    knows (:func:`_commands`) at each key the limiter named, and what it may run beyond what
    :data:`_NOBODY` may, which no grant can refuse (``AUTH``, ``HELLO``), is what the limiter
    needs beyond that too, no more and no less.  Keys: its rules as the server reads them
    (``ACL GETUSER``) hold no selector, and no key pattern outside the prefix the app's
    limiter writes every key under (``LIMITS:``); and each command the limiter needs that
    named a key, asked as first sent, may run, and asked with :data:`_ELSEWHERE` for that
    key, is refused it."""
    password = secrets.token_hex(16)
    rules = _rules_where_shekel(tmp_path, _shekel_tokens(), also=_CENSUS_USERS)
    with _served(password, ["--env", f"VALKEY_PASSWORD={password}"], rules) as store:
        answers, seen = _login_watched(store)
        assert [answer.status_code for answer in answers] == [200] * _LOGIN_LIMIT + [429]
        # The prefix as the app's limiter, just built for that run, writes it.
        prefix = limiter.storage.prefixed_key("")
        census = store.client(username=_CENSUS, password=_NOPASS)
        commands = _commands(store)
        sent = _first_sent(census, commands, seen)
        needed = _needed(store, sent, _outcome(answers))
        named = sorted({key.decode() for _, keys in sent.values() for key in keys})
        assert named, f"MONITOR showed the limiter naming no key: {seen}"
        for key in named:
            nobody = _may_run(census, _NOBODY, commands, key)
            assert _may_run(census, _APP_USER, commands, key) - nobody == needed - nobody
        assert _held_outside(census, prefix) == ([], [])
        aimed = {name: _aimed_elsewhere(census, argv, keys)
                 for name, (argv, keys) in sorted(sent.items()) if name in needed and keys}
        assert aimed == {name: (b"OK", _ELSEWHERE.encode()) for name in aimed}


def test_start_sh_writes_the_rules_only_its_user_reads_under_any_umask():
    """``start.sh`` run straight, the image's entrypoint (which sets 0077 only where the umask
    is docker's default 0022) cleared, so under that 0022: the generated ACL file, which
    holds the password's hash, is still 0600, as the umask the script sets itself makes it."""
    password = secrets.token_hex(16)
    with _served(password, ["--entrypoint", "", "--env", f"VALKEY_PASSWORD={password}"]) as store:
        mode = _docker("exec", store.name, "stat", "-c", "%a", "/tmp/users.acl")
    assert (mode.returncode, mode.stdout.strip()) == (0, "600"), mode.stderr


#: Each start ``start.sh`` refuses, and what it says.
_REFUSALS = {
    "an empty password": "VALKEY_PASSWORD is empty or unset, so the shekel user would have no "
                         "password; refusing to start",
    "no shekel user": "/etc/valkey/users.acl has no line starting 'user shekel ', so the app "
                      "could not log in; refusing to start",
}


@pytest.mark.parametrize("case", list(_REFUSALS))
def test_start_sh_refuses_a_server_the_app_could_not_log_in_to(tmp_path, case):
    """An empty password, or rules naming no ``shekel`` user: either way the app could not log
    in, and the fail-closed limiter would fail every limited request while the healthcheck,
    as its own user, passed.  ``start.sh`` exits 1 naming why, and no server starts."""
    name = f"shekel-test-valkey-{secrets.token_hex(4)}"
    rules = _rules_where_shekel(tmp_path, None) if case == "no shekel user" else _RULES
    password = "" if case == "an empty password" else secrets.token_hex(16)
    sockets = f"type=bind,source={_sockets_folder(tmp_path)}"
    try:
        # ``--quiet``: on a daemon that has not pulled the image, the CLI writes its own
        # "Unable to find image" and the pull's progress to stderr first (measured
        # 2026-11-01), and the assertion is of start.sh's line alone.
        ran = _docker("run", "--rm", "--quiet", "--env", f"VALKEY_PASSWORD={password}",
                      *_service_args(name, rules, sockets))
    finally:
        _docker("rm", "--force", name)
    assert (ran.returncode, ran.stderr) == (1, f"start.sh: {_REFUSALS[case]}\n")


#: The user and group the app runs as: the Dockerfile's one ``USER``.
(_APP_RUNS_AS,) = re.findall(r"^USER (\S+)$",
                             (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8"),
                             re.MULTILINE)
#: A user and group that neither image runs as and the volume's options do not name.
_STRANGER = "4242:4242"
#: How long a container standing in for the app holds the socket volume, far past the test.
_HOLD_S = 120


def _volume_options(mount: dict) -> str:
    """The ``--mount`` options a declared volume mount takes beyond its type, source and
    target: ``volume-nocopy`` where it declares ``nocopy``."""
    return ",volume-nocopy" if mount.get("volume", {}).get("nocopy") else ""


def _as_the_app(user: str, sockets: str, *command: str) -> list[str]:
    """``docker run``'s arguments for ``command`` run as ``user`` (``uid:gid``) in the valkey
    image, with the socket volume ``sockets`` mounted as the app service mounts it, under the
    app service's lock-downs."""
    readonly = ",readonly" if _APP_SOCKETS.get("read_only") else ""
    return ["--user", user, "--network", "none", "--entrypoint", "",
            "--mount", f"type=volume,source={sockets},target={_APP_SOCKETS['target']}"
                       f"{readonly}{_volume_options(_APP_SOCKETS)}",
            *_hardening(_APP), _VALKEY["image"], *command]


def _granted_as(user: str, sockets: str, password: str) -> tuple[int, str]:
    """What a client run as ``user`` (:func:`_as_the_app`) says when it runs a command the
    ``shekel`` user is granted: its exit status, and its output and errors."""
    ran = _docker("run", "--rm", *_as_the_app(
        user, sockets, "valkey-cli", "-s", str(_SOCKET), "--user", _APP_USER, "-a", password,
        "--no-auth-warning", "lpush", "LIMITS:a", "1"))
    return ran.returncode, (ran.stdout + ran.stderr).strip()


#: The server's user as declared, and that user with a group of the stranger's in place of
#: its own: the volume's setgid bit, not the server's group, decides the socket's.
_SERVER_USERS = (_VALKEY["user"], f"{_VALKEY['user']}:{_STRANGER.split(':')[1]}")


@pytest.mark.parametrize("held", [False, True], ids=["fresh", "held"])
@pytest.mark.parametrize("server_user", _SERVER_USERS)
def test_the_socket_volume_admits_the_app_s_user_alone(server_user, held):
    """The socket's folder as production makes it: ``docker-compose.yml``'s volume, made with
    the driver options it declares, mounted by the valkey service as declared.  A client run
    as the app's user and group, with the volume mounted as the app service mounts it
    (read-only), reaches the server and runs a granted command as ``shekel``; a client run as
    another user and group is refused the socket.  So the volume lets the server's user make
    its socket, hands the socket the app's group, and lets no one else in.  The same holds
    with the server's own group a stranger's: the socket takes the volume's group, so the app
    is admitted whatever group the valkey image gives its user.  And it holds ``held``, the
    server created while a container mounted as the app already holds the volume empty, as a
    recreate after a clean stop meets it: there docker copies the valkey image's own
    ``/run/valkey`` over the volume's root, its mode and setgid bit with it, unless the
    mounts say ``nocopy``."""
    password = secrets.token_hex(16)
    name = f"shekel-test-valkey-{secrets.token_hex(4)}"
    holder = f"shekel-test-valkey-holder-{secrets.token_hex(4)}"
    sockets = f"shekel-test-valkey-sockets-{secrets.token_hex(4)}"
    options = [argument for key, value in _SOCKET_VOLUME["driver_opts"].items()
               for argument in ("--opt", f"{key}={value}")]
    try:
        made = _docker("volume", "create", *options, sockets)
        assert made.returncode == 0, made.stderr
        if held:
            holding = _docker("run", "--detach", "--name", holder,
                              *_as_the_app(_APP_RUNS_AS, sockets, "sleep", str(_HOLD_S)))
            assert holding.returncode == 0, holding.stderr
        _started(name, ["--env", f"VALKEY_PASSWORD={password}",
                        *_service_args(name, _RULES, f"type=volume,source={sockets}"
                                       f"{_volume_options(_SOCKETS)}", server_user)])
        answers = {user: _granted_as(user, sockets, password)
                   for user in (_APP_RUNS_AS, _STRANGER)}
    finally:
        _docker("rm", "--force", name, holder)
        _docker("volume", "rm", "--force", sockets)
    assert answers == {
        _APP_RUNS_AS: (0, "1"),
        _STRANGER: (1, f"Could not connect to Valkey at {_SOCKET}: Permission denied")}
