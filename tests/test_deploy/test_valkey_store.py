"""The rate limiter's real store (ruling R-BAL298): the pinned Valkey image, started as the
compose files start it, under the committed ACL, driven by the app's own limiter.

The suite runs the limiter on ``memory://`` (TestConfig), which refuses no command, so a grant
missing from ``deploy/valkey/users.acl`` would pass it and fail in production, where the
limiter is fail-closed (``RATELIMIT_SWALLOW_ERRORS`` off, no in-memory fallback) and every
rate-limited request would fail.  These tests run the store as production does:

* The container is ``docker-compose.yml``'s ``valkey`` service as that file declares it
  (:func:`_service_args`): its image by tag and digest, its command (``sh
  /etc/valkey/start.sh``), its user, its read-only root and tmpfs mounts, its dropped
  capabilities, its security options and its bind of ``deploy/valkey/`` at ``/etc/valkey``.
  Not its resource caps (a rootless daemon need not delegate the cgroup controllers they
  use), its network (the test reaches it on a loopback port) or its healthcheck (the test
  waits on the healthcheck user's PING itself).
* The app's own limiter (Flask-Limiter on limits' moving-window strategy, BaseConfig's
  settings) drives ``/login``'s ``5 per 15 minutes`` as the ``shekel`` user: each request's
  acquire (``acquire_moving_window.lua``) and the window read behind its X-RateLimit-*
  headers (``moving_window.lua``), each run by EVALSHA on a server that holds no script yet,
  so valkey-py loads each by SCRIPT LOAD first.
* Each grant of the ``shekel`` user's is graded as one the limiter needs: the limiter
  passes under the committed rules, and fails under them with any one grant taken out,
  each read from the committed file.  A grant widened in place (``+@list`` for the list
  commands) still passes; task plan#2563 censuses the user's grants whole.

``docker``-marked: ``scripts/test.sh`` deselects them unless asked
(``PYTEST_MARKER_EXPR=docker``), CI runs them, and this package's conftest refuses the
system daemon outside CI.  A container that does not start FAILS the test rather than
skipping it: a skip would pass the gate these tests exist to be.
"""
from __future__ import annotations

import contextlib
import secrets
import shutil
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import valkey
from valkey.exceptions import AuthenticationError, NoPermissionError
from werkzeug.test import TestResponse

from app import create_app
from app.extensions import db, limiter
from tests.conftest import SEED_USER_EMAIL
from tests.test_deploy._compose import REPO_ROOT, service

pytestmark = pytest.mark.docker

#: The valkey service as ``docker-compose.yml`` declares it.
_VALKEY = service("valkey")
#: The service's one mount: ``deploy/valkey/``, bound read-only at ``/etc/valkey``.
(_BIND,) = _VALKEY["volumes"]
#: The rules and start script the service mounts, as committed.
_RULES = (REPO_ROOT / _BIND["source"]).resolve()
#: Where the test reaches a server: a port published on loopback only.
_HOST = "127.0.0.1"
#: The port the server listens on inside its container.
_SERVER_PORT = "6379/tcp"
#: Where ``start.sh`` reads a mounted password file from.
_SECRET = "/run/secrets/valkey_password"
#: How ``users.acl`` starts the ``shekel`` user's one line.
_SHEKEL = "user shekel "
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


def _service_args(name: str, rules: Path) -> list[str]:
    """``docker run``'s arguments for the valkey service, named ``name``, with ``rules``
    mounted where the service mounts ``deploy/valkey/``: each option :data:`_VALKEY` declares
    that shapes how the server starts and as whom, then its image and command."""
    readonly = ",readonly" if _BIND["read_only"] else ""
    args = ["--name", name, "--user", _VALKEY["user"],
            "--mount", f"type=bind,source={rules},target={_BIND['target']}{readonly}"]
    if _VALKEY["read_only"]:
        args.append("--read-only")
    for path in _VALKEY["tmpfs"]:
        args += ["--tmpfs", path]
    for capability in _VALKEY["cap_drop"]:
        args += ["--cap-drop", capability]
    for option in _VALKEY["security_opt"]:
        args += ["--security-opt", option]
    return [*args, _VALKEY["image"], *_VALKEY["command"]]


@dataclass(frozen=True)
class _Store:
    """A running server: its container's ``name``, the loopback port the test reaches it on,
    and the password of its ``shekel`` user."""

    name: str
    port: int
    password: str

    @property
    def uri(self) -> str:
        """The store as the app names it: the ``shekel`` user on this server."""
        return f"valkey://shekel:{self.password}@{_HOST}:{self.port}/0"

    def client(self, username: str | None = None, password: str | None = None
               ) -> valkey.Valkey:
        """A client of this server, logged in as ``username`` (none: not logged in)."""
        return valkey.Valkey(host=_HOST, port=self.port, username=username, password=password,
                             socket_timeout=_READY_S)


def _answers(store: _Store) -> bool:
    """Whether the server answers its healthcheck user's PING, the compose healthcheck's test."""
    try:
        return store.client(username="healthcheck", password="").ping()
    except (valkey.exceptions.ConnectionError, valkey.exceptions.TimeoutError):
        return False


def _logs(name: str) -> str:
    """What the container ``name`` has written, its output then its errors."""
    logs = _docker("logs", name)
    return logs.stdout + logs.stderr


@contextlib.contextmanager
def _served(password: str, given: list[str], rules: Path = _RULES) -> Iterator[_Store]:
    """Run the valkey service with ``rules`` mounted, handed its ``shekel`` password
    ``password`` by the options ``given``, on a free loopback port, until it answers; yield
    it, then remove its container whatever happened.

    Raises:
        AssertionError: when the container does not start or publish its port, or the server
            does not answer within :data:`_READY_S`, naming what docker or the server said.
    """
    name = f"shekel-test-valkey-{secrets.token_hex(4)}"
    try:
        started = _docker("run", "--detach", "--publish", f"{_HOST}::{_SERVER_PORT}", *given,
                          *_service_args(name, rules))
        assert started.returncode == 0, f"the valkey service did not start: {started.stderr}"
        published = _docker("port", name, _SERVER_PORT)
        assert published.returncode == 0, f"no port was published: {published.stderr}"
        store = _Store(name, int(published.stdout.split()[0].rsplit(":", 1)[1]), password)
        deadline = time.monotonic() + _READY_S
        while not _answers(store):
            assert time.monotonic() <= deadline, (
                f"the valkey service did not answer within {_READY_S} s:\n{_logs(name)}")
            time.sleep(0.1)
        yield store
    finally:
        _docker("rm", "--force", name)


@pytest.fixture(name="store")
def _store() -> Iterator[_Store]:
    """A fresh server, handed its ``shekel`` password in ``VALKEY_PASSWORD`` as the base and
    dev compose files hand it."""
    password = secrets.token_hex(16)
    with _served(password, ["--env", f"VALKEY_PASSWORD={password}"]) as store:
        yield store


def _login_answers(uri: str) -> list[TestResponse]:
    """``/login``'s limit and one more POSTs, through the app's own limiter on the store
    ``uri``, each answer in turn.  The limiter is fail-closed, so a command the store refuses
    raises out of the request (a :class:`valkey.exceptions.ValkeyError`)."""
    rate_app = create_app("testing")
    rate_app.config.update(RATELIMIT_ENABLED=True, RATELIMIT_STORAGE_URI=uri)
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


@pytest.mark.usefixtures("seed_user")
def test_a_limited_route_runs_on_the_store_as_the_app_s_user(store):
    """``/login``'s five POSTs pass on the store, each answered with the window it read
    (X-RateLimit-Remaining 4 down to 0), and the sixth is refused, with when to retry: the
    acquire and the window read each ran, by EVALSHA after SCRIPT LOAD on the fresh server,
    as the ``shekel`` user.  A grant missing from the ACL fails a request instead."""
    answers = _login_answers(store.uri)
    assert [answer.status_code for answer in answers] == [200] * _LOGIN_LIMIT + [429]
    assert [answer.headers.get("X-RateLimit-Remaining") for answer in answers] == [
        "4", "3", "2", "1", "0", "0"]
    assert int(answers[-1].headers["Retry-After"]) > 0


#: What the ``shekel`` user may not run, each as sent: what ``users.acl`` names as not
#: granted (PING, DEL, TTL, KEYS), two no limiter sends (FLUSHALL, CONFIG), and a granted
#: command on a key outside ``LIMITS:*``.
_OUTSIDE = (("PING",), ("DEL", "LIMITS:a"), ("TTL", "LIMITS:a"), ("KEYS", "LIMITS:*"),
            ("FLUSHALL",), ("CONFIG", "GET", "maxmemory"), ("LPUSH", "elsewhere", "1"))


def test_every_command_outside_the_app_s_grant_is_refused(store):
    """The ``shekel`` user is refused with NOPERM each command its grant leaves out and a
    granted one on a key outside its pattern; a client not logged in is refused everything,
    since the rules switch the default user off."""
    shekel = store.client(username="shekel", password=store.password)
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
        shekel = store.client(username="shekel", password=password)
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


def _rules_where_shekel(tmp_path: Path, tokens: list[str] | None) -> Path:
    """A copy of the committed rules and start script whose ``users.acl`` gives the ``shekel``
    user ``tokens`` after its name, or names no ``shekel`` user where ``tokens`` is None, its
    one line dropped."""
    rules = tmp_path / "rules"
    shutil.copytree(_RULES, rules)
    acl = rules / "users.acl"
    acl.write_text("".join(
        line if not line.startswith(_SHEKEL)
        else "" if tokens is None else f"{_SHEKEL}{' '.join(tokens)}\n"
        for line in acl.read_text(encoding="utf-8").splitlines(keepends=True)),
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
        answers = _login_answers(store.uri)
    assert [answer.status_code for answer in answers] == [200] * _LOGIN_LIMIT + [429]


@pytest.mark.usefixtures("seed_user")
@pytest.mark.parametrize("grant", _GRANTS)
def test_each_grant_of_the_app_s_user_is_one_the_limiter_needs(tmp_path, grant):
    """The ``shekel`` user's line with one grant taken out, and the store refuses the
    limiter's requests for that grant (:data:`_REFUSED`): so each token the user holds is one
    the limiter needs, where :func:`test_a_limited_route_runs_on_the_store_as_the_app_s_user`
    shows it holds all it does.  The grants are read from the committed file, so one added
    there (``+get``, ``allkeys``, ``&*``) is graded with no edit here; a grant widened in
    place is not (task plan#2563)."""
    password = secrets.token_hex(16)
    rules = _rules_where_shekel(tmp_path, [token for token in _shekel_tokens()
                                           if token != grant])
    with _served(password, ["--env", f"VALKEY_PASSWORD={password}"], rules) as store:
        with pytest.raises(valkey.exceptions.ValkeyError, match=_REFUSED):
            _login_answers(store.uri)


def test_start_sh_writes_the_rules_only_its_user_reads_under_any_umask():
    """``start.sh`` run straight, the image's entrypoint (which sets 0077 only where the umask
    is docker's default 0022) cleared, so under that 0022: the generated ACL file, which holds the password's
    hash, is still 0600, as the umask the script sets itself makes it."""
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
    try:
        # ``--quiet``: on a daemon that has not pulled the image, the CLI writes its own
        # "Unable to find image" and the pull's progress to stderr first (measured
        # 2026-11-01), and the assertion is of start.sh's line alone.
        ran = _docker("run", "--rm", "--quiet", "--env", f"VALKEY_PASSWORD={password}",
                      *_service_args(name, rules))
    finally:
        _docker("rm", "--force", name)
    assert (ran.returncode, ran.stderr) == (1, f"start.sh: {_REFUSALS[case]}\n")
