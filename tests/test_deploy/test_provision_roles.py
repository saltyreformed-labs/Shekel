"""The database's two logins and its owner, made by ``deploy/postgres/provision_roles.sh``
inside the database container (rulings plan#3412, plan#3413).

Each test starts its own cluster from the image ``docker-compose.yml``'s ``db`` service pins,
run as that service runs it (its user, a read-only root with tmpfs at the paths it writes, no
capabilities, no new privileges), on the daemon's default bridge, and the script mounted as the
image's first-boot hook.  The script's two callers are each graded: the first boot, which runs
the hook once while the cluster is made, and an operator's ``docker exec`` of the same file in
the running container.  A login is graded by logging in with its password at the container's
BRIDGE address, as the app and deploy containers reach it over a network: the image's
``pg_hba.conf`` trusts the socket and loopback with no password at all (initdb's default; a
login at 127.0.0.1 with a wrong password measured accepted, 2026-11-10), and asks a password of
every other address.  The superuser's work runs over the socket, which is how an operator's run
reaches it with no password.

The last two classes run what the deploy then runs as the owner, ``scripts/init_db.sql`` and
``scripts/init_db_role.sql`` (``scripts/deploy.sh``'s first two steps), each as the deploy runs
it, logged in as the owner at the bridge address, so a grant that needs a superuser's right
fails here.  The last pauses the grants file between its blanket grant and its revoke and reads,
from another session, what the web login holds then.

``docker``-marked: ``scripts/test.sh`` deselects them unless asked
(``PYTEST_MARKER_EXPR=docker``), CI runs them, and this package's conftest refuses the system
daemon outside CI.  A cluster that does not start FAILS the test rather than skipping it.
"""
from __future__ import annotations

import contextlib
import secrets
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import pytest

from tests.test_deploy._compose import REPO_ROOT, service

pytestmark = pytest.mark.docker

#: The db service as ``docker-compose.yml`` declares it.
_DB = service("db")
#: The script under test, as committed.
_SCRIPT = REPO_ROOT / "deploy" / "postgres" / "provision_roles.sh"
#: The deploy's two SQL files, as committed, in the order ``scripts/deploy.sh`` runs them.
_SCHEMAS_SQL = REPO_ROOT / "scripts" / "init_db.sql"
_GRANTS_SQL = REPO_ROOT / "scripts" / "init_db_role.sql"
#: What ``scripts/deploy.sh`` hands psql for each file beyond what every one of its runs takes:
#: the grants run as one transaction (ruling plan#3534; deploy.sh's side is pinned by
#: ``test_entrypoints.py``, ``test_grants_the_web_login_its_privileges_in_one_transaction``).
_DEPLOY_OPTIONS = {_SCHEMAS_SQL: (), _GRANTS_SQL: ("--single-transaction",)}
#: Where the image runs a first-boot hook from, and where an operator runs the script.
_HOOK = "/docker-entrypoint-initdb.d/10-provision-roles.sh"
#: The superuser and database the service's environment names.
_SUPERUSER = _DB["environment"]["POSTGRES_USER"]
_DATABASE = _DB["environment"]["POSTGRES_DB"]
_OWNER = "shekel_owner"
_APP = "shekel_app"
#: How long one docker command may take, a cold pull of the pinned image included.
_DOCKER_TIMEOUT_S = 90
#: How long a started container has to accept a TCP login, its first boot included.
_READY_S = 60.0
#: How long a session the test waits on may take to reach the state it waits for (a grant
#: held open, a file waiting on it): each is a statement or two away, so 30 s is generous.
_AWAIT_S = 30.0
#: How often that wait reads the database.
_POLL_S = 0.2
#: Every login attribute the script pins off, as ``pg_roles`` names them.
_ESCALATING = ("rolsuper", "rolcreatedb", "rolcreaterole", "rolreplication", "rolbypassrls")
#: Objects outside the system catalogs not owned by the owner: schemas, relations (indexes,
#: sequences and row types among them), routines and types.  The count the hand-over must
#: bring to zero.
_NOT_THE_OWNERS = f"""
SELECT (SELECT count(*) FROM pg_namespace
        WHERE nspname !~ '^pg_' AND nspname NOT IN ('information_schema', 'public')
          AND nspowner <> '{_OWNER}'::regrole)
     + (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
          AND c.relowner <> '{_OWNER}'::regrole)
     + (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
          AND p.proowner <> '{_OWNER}'::regrole)
     + (SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
          AND t.typowner <> '{_OWNER}'::regrole)
"""
#: A schema of every kind of object the app's migrations make, made by the superuser as
#: every database before this script was (the census of 2026-11-10 over a production-shaped
#: database: schemas, tables with their indexes and column sequences, and functions, one of
#: them a trigger's), with a grant to the app login made before the hand-over.
_APP_SHAPED_SCHEMA = f"""
CREATE SCHEMA budget;
CREATE SCHEMA system;
CREATE TABLE public.alembic_version (version_num varchar(32) PRIMARY KEY);
CREATE TABLE budget.accounts (id serial PRIMARY KEY, name text NOT NULL);
CREATE TABLE budget.entries (id integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                             account_id integer REFERENCES budget.accounts (id), amount numeric);
CREATE INDEX idx_entries_account ON budget.entries (account_id);
CREATE TABLE system.audit_log (id bigserial PRIMARY KEY, row_data text);
CREATE FUNCTION system.audit_trigger_func() RETURNS trigger LANGUAGE plpgsql AS
    $$BEGIN INSERT INTO system.audit_log (row_data) VALUES (NEW::text); RETURN NEW; END$$;
CREATE TRIGGER audit_entries AFTER INSERT ON budget.entries
    FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func();
CREATE FUNCTION budget.assert_balanced() RETURNS integer LANGUAGE sql AS $$SELECT 1$$;
GRANT USAGE ON SCHEMA budget, system TO {_APP};
GRANT SELECT, INSERT ON budget.accounts, budget.entries TO {_APP};
GRANT SELECT, INSERT ON system.audit_log TO {_APP};
GRANT USAGE ON ALL SEQUENCES IN SCHEMA budget, system TO {_APP};
"""
#: The posting ledger's two tables, as much of them as the grants file's revoke needs.
_LEDGER = """
CREATE TABLE budget.journal_entries (id serial PRIMARY KEY);
CREATE TABLE budget.account_postings (
    id serial PRIMARY KEY,
    journal_entry_id integer NOT NULL REFERENCES budget.journal_entries (id) ON DELETE CASCADE);
"""
#: ``t`` when the web login holds UPDATE or DELETE on either ledger table, as the committed
#: catalog says, else ``f``.
_LEDGER_OPEN_TO_APP = f"""
SELECT bool_or(has_table_privilege('{_APP}', ledger, privilege))
FROM unnest(ARRAY['budget.journal_entries', 'budget.account_postings']) AS ledger,
     unnest(ARRAY['UPDATE', 'DELETE']) AS privilege
"""
#: The application name the session that pauses the grants file connects under.
_BLOCKER = "shekel-test-blocker"


def _password() -> str:
    """A password the script accepts: random, and well over its 16-character floor."""
    return secrets.token_urlsafe(24)


def _docker(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run ``docker`` with ``args``, its output captured; its exit status is the caller's."""
    return subprocess.run(["docker", *args], input=stdin, capture_output=True, text=True,
                          timeout=_DOCKER_TIMEOUT_S, check=False)


@dataclass
class _Cluster:
    """A running cluster: its container, and the host folder mounted at ``/run/secrets``."""

    container: str
    secrets_dir: Path
    address: str

    def superuser(self, sql: str) -> str:
        """Run ``sql`` as the bootstrap superuser over the socket; its tuples-only output.

        Raises:
            AssertionError: when psql exits non-zero, with its error.
        """
        done = _docker("exec", "-i", self.container, "psql", "-U", _SUPERUSER, "-d", _DATABASE,
                       "-v", "ON_ERROR_STOP=1", "--no-psqlrc", "-tAq", stdin=sql)
        assert done.returncode == 0, f"superuser SQL failed: {done.stderr}"
        return done.stdout.strip()

    def login(self, role: str, password: str, sql: str = "SELECT current_user"
              ) -> subprocess.CompletedProcess[str]:
        """Log in as ``role`` with ``password`` at the container's bridge address, where a
        password is asked, and run ``sql``; the completed run."""
        return _docker("exec", "-e", f"PGPASSWORD={password}", self.container, "psql",
                       "-h", self.address, "-U", role, "-d", _DATABASE, "-v", "ON_ERROR_STOP=1",
                       "--no-psqlrc", "-tAqc", sql)

    def file_command(self, role: str, password: str, sql_file: Path) -> list[str]:
        """``docker``'s arguments to log in as ``role`` with ``password`` at the bridge address
        and run the deploy's ``sql_file``, read from standard input, as ``scripts/deploy.sh``
        runs it: stopping at its first error, with that file's options."""
        return ["exec", "-i", "-e", f"PGPASSWORD={password}", self.container, "psql",
                "-h", self.address, "-U", role, "-d", _DATABASE, "-v", "ON_ERROR_STOP=1",
                "--no-psqlrc", "-q", *_DEPLOY_OPTIONS[sql_file], "-f", "-"]

    def run_file(self, role: str, password: str, sql_file: Path
                 ) -> subprocess.CompletedProcess[str]:
        """Log in as ``role`` with ``password`` at the bridge address and run the deploy's
        ``sql_file`` as ``scripts/deploy.sh`` runs it."""
        return _docker(*self.file_command(role, password, sql_file),
                       stdin=sql_file.read_text(encoding="utf-8"))

    def provision(self, *env: str) -> subprocess.CompletedProcess[str]:
        """Run the script as an operator does, in the running container, with ``env``'s
        ``NAME=value`` pairs added to the container's environment for that run."""
        flags = [flag for pair in env for flag in ("-e", pair)]
        return _docker("exec", *flags, self.container, _HOOK)


def _run_args(name: str, secrets_dir: Path, env: dict[str, str]) -> list[str]:
    """``docker run``'s arguments for the db service named ``name``: its image, user,
    read-only root with its tmpfs mounts, dropped capabilities and security options, a fresh
    volume for its data, ``secrets_dir`` at ``/run/secrets`` and the script as its hook."""
    args = ["run", "-d", "--name", name, "--user", _DB["user"],
            "--read-only", "--cap-drop", "ALL"]
    for option in _DB["security_opt"]:
        args += ["--security-opt", option]
    for path in _DB["tmpfs"]:
        args += ["--tmpfs", path]
    # The service's named volume, as a fresh anonymous one: docker copies the image's own
    # /var/lib/postgresql into it, owned by the postgres user, as it does for the named one.
    args += ["--mount", "type=volume,dst=/var/lib/postgresql"]
    args += ["-v", f"{secrets_dir}:/run/secrets:ro", "-v", f"{_SCRIPT}:{_HOOK}:ro"]
    for key, value in {"POSTGRES_USER": _SUPERUSER, "POSTGRES_DB": _DATABASE,
                       "POSTGRES_PASSWORD": _password(), **env}.items():
        args += ["-e", f"{key}={value}"]
    return [*args, _DB["image"]]


@contextlib.contextmanager
def _started(tmp_path: Path, **env: str) -> Iterator[_Cluster]:
    """A cluster started with ``env`` added to the service's environment, once it accepts a
    TCP connection; removed, with its volume, afterwards.

    Raises:
        AssertionError: when the container will not start, or stops or never accepts a
            connection, with its log.
    """
    name = f"shekel-test-provision-{secrets.token_hex(4)}"
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir(mode=0o755)
    started = _docker(*_run_args(name, secrets_dir, env))
    try:
        assert started.returncode == 0, f"docker run failed: {started.stderr}"
        _wait_ready(name)
        address = _docker("inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}"
                          "{{end}}", name).stdout.strip()
        assert address, f"{name} has no bridge address"
        yield _Cluster(name, secrets_dir, address)
    finally:
        _docker("rm", "-f", "-v", name)


def _wait_ready(name: str) -> None:
    """Wait for ``name`` to accept a TCP connection: the server the image starts for its first
    boot listens on its socket only, so this waits past the hook to the real server.

    Raises:
        AssertionError: when the container stops, or the wait runs out, with its log.
    """
    deadline = time.monotonic() + _READY_S
    while time.monotonic() < deadline:
        if _docker("exec", name, "pg_isready", "-h", "127.0.0.1", "-U", _SUPERUSER,
                   "-d", _DATABASE, "-q").returncode == 0:
            return
        state = _docker("inspect", "-f", "{{.State.Running}}", name).stdout.strip()
        if state != "true":
            break
        time.sleep(0.5)
    log = _docker("logs", name)
    raise AssertionError(f"{name} never accepted a connection:\n{log.stdout}\n{log.stderr}")


@contextlib.contextmanager
def _background(args: list[str], stdin: int | IO[str]) -> Iterator[subprocess.Popen[str]]:
    """``docker`` with ``args``, started in the background reading ``stdin``, its output
    captured; killed on the way out if it is still running, and waited for."""
    with subprocess.Popen(["docker", *args], stdin=stdin, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True) as process:
        try:
            yield process
        finally:
            if process.poll() is None:
                process.kill()


def _await_row(cluster: _Cluster, sql: str, what: str) -> str:
    """Read ``sql`` as the superuser until it returns a row; that row.

    Raises:
        AssertionError: when none comes within ``_AWAIT_S``, naming ``what``.
    """
    deadline = time.monotonic() + _AWAIT_S
    while time.monotonic() < deadline:
        row = cluster.superuser(sql)
        if row:
            return row
        time.sleep(_POLL_S)
    raise AssertionError(f"never saw {what} within {_AWAIT_S} s")


def _first_boot_refused(tmp_path: Path, **env: str) -> str:
    """Start a cluster whose first boot must refuse; the container's log once it has stopped.

    Raises:
        AssertionError: when the container accepts a connection instead, or does not stop.
    """
    name = f"shekel-test-provision-{secrets.token_hex(4)}"
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir(mode=0o755)
    started = _docker(*_run_args(name, secrets_dir, env))
    try:
        assert started.returncode == 0, f"docker run failed: {started.stderr}"
        deadline = time.monotonic() + _READY_S
        while _docker("inspect", "-f", "{{.State.Running}}", name).stdout.strip() == "true":
            assert time.monotonic() < deadline, "the first boot did not refuse: still running"
            time.sleep(0.5)
        status = _docker("inspect", "-f", "{{.State.ExitCode}}", name).stdout.strip()
        assert status not in ("", "0"), f"the first boot did not refuse: exit {status!r}"
        log = _docker("logs", name)
        return log.stdout + log.stderr
    finally:
        _docker("rm", "-f", "-v", name)


def _attributes(cluster: _Cluster, role: str) -> dict[str, str]:
    """``role``'s login and escalating attributes as ``pg_roles`` holds them, ``t`` or ``f``."""
    columns = ("rolcanlogin", *_ESCALATING)
    row = cluster.superuser(f"SELECT {', '.join(columns)} FROM pg_roles "
                            f"WHERE rolname = '{role}'")
    assert row, f"no role {role}"
    return dict(zip(columns, row.split("|"), strict=True))


@pytest.mark.timeout(120)
class TestFirstBoot:
    """The hook the image runs once, when it makes the cluster."""

    def test_makes_both_logins_with_their_passwords_and_no_escalating_attribute(
        self, tmp_path: Path,
    ) -> None:
        """A first boot makes ``shekel_owner`` and ``shekel_app``, each logging in with its
        own password and holding no attribute that reaches past the database's rows, and the
        owner owns the database."""
        owner, app = _password(), _password()
        with _started(tmp_path, OWNER_ROLE_PASSWORD=owner, APP_ROLE_PASSWORD=app) as cluster:
            for role, password in ((_OWNER, owner), (_APP, app)):
                assert cluster.login(role, password).stdout.strip() == role
                assert _attributes(cluster, role) == {
                    "rolcanlogin": "t", **dict.fromkeys(_ESCALATING, "f")}
            assert cluster.superuser(
                "SELECT pg_get_userbyid(datdba) FROM pg_database "
                f"WHERE datname = '{_DATABASE}'") == _OWNER
            # Each password is its own login's alone.
            assert cluster.login(_APP, owner).returncode != 0
            assert cluster.login(_OWNER, app).returncode != 0

    def test_a_short_password_stops_the_first_boot(self, tmp_path: Path) -> None:
        """A password under 16 characters stops the cluster's creation, naming where it came
        from and its length, and never its value."""
        short = "fifteen-chars!!"
        log = _first_boot_refused(tmp_path, OWNER_ROLE_PASSWORD=short,
                                  APP_ROLE_PASSWORD=_password())
        assert "the password in OWNER_ROLE_PASSWORD is shorter than 16 characters (got 15)" in log
        assert short not in log

    def test_a_missing_password_stops_the_first_boot(self, tmp_path: Path) -> None:
        """No file and no variable for a login stops the cluster's creation by name."""
        log = _first_boot_refused(tmp_path, OWNER_ROLE_PASSWORD=_password())
        assert "no password for APP_ROLE_PASSWORD: APP_ROLE_PASSWORD is empty or unset" in log


@pytest.mark.timeout(120)
class TestOperatorRun:
    """The same file, run by hand in the running container."""

    def test_hands_a_superuser_owned_database_to_the_owner(self, tmp_path: Path) -> None:
        """Every object the app's migrations make, made by the superuser as before this
        script, is the owner's after one run: the database, its schemas, its tables with
        their indexes and sequences, its functions.  The owner can then do what a migration
        does, and the app login keeps the grants it had."""
        owner, app = _password(), _password()
        with _started(tmp_path, OWNER_ROLE_PASSWORD=owner, APP_ROLE_PASSWORD=app) as cluster:
            cluster.superuser(_APP_SHAPED_SCHEMA)
            assert int(cluster.superuser(_NOT_THE_OWNERS)) > 0

            ran = cluster.provision()

            assert ran.returncode == 0, ran.stderr
            assert int(cluster.superuser(_NOT_THE_OWNERS)) == 0
            # Two schemas, four tables (alembic_version, accounts, entries, audit_log) and two
            # functions: 8.  The database was the owner's from the first boot.
            assert "8 object(s) handed to shekel_owner" in ran.stderr
            migration = cluster.login(_OWNER, owner, (
                "ALTER TABLE budget.entries ADD COLUMN memo text; "
                "DROP TRIGGER audit_entries ON budget.entries; "
                "CREATE TRIGGER audit_entries AFTER INSERT ON budget.entries "
                "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func(); "
                "CREATE OR REPLACE FUNCTION budget.assert_balanced() RETURNS integer "
                "LANGUAGE sql AS 'SELECT 2'; "
                "CREATE SCHEMA salary; "
                "UPDATE public.alembic_version SET version_num = 'b' "))
            assert migration.returncode == 0, migration.stderr
            written = cluster.login(_APP, app, (
                "INSERT INTO budget.accounts (name) VALUES ('Checking') RETURNING id"))
            assert written.returncode == 0, written.stderr

    def test_a_second_run_changes_no_login_or_owner(self, tmp_path: Path) -> None:
        """Run again with the same passwords, it hands over nothing and every login logs in as
        before, with the same attributes."""
        owner, app = _password(), _password()
        with _started(tmp_path, OWNER_ROLE_PASSWORD=owner, APP_ROLE_PASSWORD=app) as cluster:
            cluster.superuser(_APP_SHAPED_SCHEMA)
            assert cluster.provision().returncode == 0
            before = {role: _attributes(cluster, role) for role in (_OWNER, _APP)}

            again = cluster.provision()

            assert again.returncode == 0, again.stderr
            assert "0 object(s) handed to shekel_owner" in again.stderr
            assert {role: _attributes(cluster, role) for role in (_OWNER, _APP)} == before
            assert int(cluster.superuser(_NOT_THE_OWNERS)) == 0
            assert cluster.login(_OWNER, owner).returncode == 0
            assert cluster.login(_APP, app).returncode == 0

    def test_a_login_altered_by_hand_is_put_back(self, tmp_path: Path) -> None:
        """An escalating attribute granted to either login by hand is taken away again by the
        next run, so the pins hold on a role the script did not just make."""
        owner, app = _password(), _password()
        with _started(tmp_path, OWNER_ROLE_PASSWORD=owner, APP_ROLE_PASSWORD=app) as cluster:
            cluster.superuser(f"ALTER ROLE {_OWNER} SUPERUSER CREATEDB CREATEROLE "
                              f"REPLICATION BYPASSRLS; ALTER ROLE {_APP} NOLOGIN CREATEROLE;")

            ran = cluster.provision()

            assert ran.returncode == 0, ran.stderr
            for role in (_OWNER, _APP):
                assert _attributes(cluster, role) == {
                    "rolcanlogin": "t", **dict.fromkeys(_ESCALATING, "f")}

    def test_a_password_file_overrides_the_variable_and_rotates_the_password(
        self, tmp_path: Path,
    ) -> None:
        """A file mounted at ``/run/secrets/owner_role_password`` is read in place of the
        variable: the run sets the owner's password to the file's (its trailing newline
        dropped), the old one stops working, and the app login, with no file, keeps the
        variable's."""
        owner, app, rotated = _password(), _password(), _password()
        with _started(tmp_path, OWNER_ROLE_PASSWORD=owner, APP_ROLE_PASSWORD=app) as cluster:
            secret = cluster.secrets_dir / "owner_role_password"
            secret.write_text(f"{rotated}\n", encoding="utf-8")
            secret.chmod(0o644)

            ran = cluster.provision()

            assert ran.returncode == 0, ran.stderr
            assert cluster.login(_OWNER, rotated).returncode == 0
            assert cluster.login(_OWNER, owner).returncode != 0
            assert cluster.login(_APP, app).returncode == 0

    def test_an_empty_password_file_is_refused_before_any_change(self, tmp_path: Path) -> None:
        """An empty mounted file is refused by name, and the database is left as it was: the
        owner's password still works and nothing is handed over."""
        owner, app = _password(), _password()
        with _started(tmp_path, OWNER_ROLE_PASSWORD=owner, APP_ROLE_PASSWORD=app) as cluster:
            cluster.superuser(_APP_SHAPED_SCHEMA)
            left = cluster.superuser(_NOT_THE_OWNERS)
            secret = cluster.secrets_dir / "owner_role_password"
            secret.write_text("", encoding="utf-8")
            secret.chmod(0o644)

            ran = cluster.provision()

            assert ran.returncode != 0
            assert ("no password for OWNER_ROLE_PASSWORD: "
                    "/run/secrets/owner_role_password is empty or unset") in ran.stderr
            assert cluster.login(_OWNER, owner).returncode == 0
            assert cluster.superuser(_NOT_THE_OWNERS) == left

    def test_an_object_of_a_kind_it_does_not_hand_over_is_refused_by_name(
        self, tmp_path: Path,
    ) -> None:
        """A view, a kind the app's migrations do not make, is refused by name, and the one
        transaction leaves every table it had already handed over with the superuser."""
        owner, app = _password(), _password()
        with _started(tmp_path, OWNER_ROLE_PASSWORD=owner, APP_ROLE_PASSWORD=app) as cluster:
            cluster.superuser(_APP_SHAPED_SCHEMA
                              + "CREATE VIEW budget.balances AS SELECT * FROM budget.entries;")

            ran = cluster.provision()

            assert ran.returncode != 0
            assert (f"the view budget.balances is owned by {_SUPERUSER}, not shekel_owner"
                    in ran.stderr)
            assert cluster.superuser(
                "SELECT pg_get_userbyid(relowner) FROM pg_class "
                "WHERE oid = 'budget.accounts'::regclass") == _SUPERUSER


@pytest.mark.timeout(120)
class TestTheDeploysSqlRunsAsTheOwner:
    """The deploy's schemas and the web login's grants, run as the owner, need no superuser."""

    def _deploy_sql(self, cluster: _Cluster, owner: str) -> None:
        """Run the deploy's two SQL files as the owner, in the deploy's order.

        Raises:
            AssertionError: when either stops, with psql's error.
        """
        for sql_file in (_SCHEMAS_SQL, _GRANTS_SQL):
            ran = cluster.run_file(_OWNER, owner, sql_file)
            assert ran.returncode == 0, f"{sql_file.name} as the owner: {ran.stderr}"

    def test_on_a_fresh_cluster_the_owner_owns_the_schemas_and_the_app_reaches_new_tables(
        self, tmp_path: Path,
    ) -> None:
        """On the first boot's empty database the owner makes every schema, so owns it, and a
        table it makes afterwards, as a migration does, takes the web login's grants from the
        default privileges the owner set: the app login reads and writes it and cannot drop
        it."""
        owner, app = _password(), _password()
        with _started(tmp_path, OWNER_ROLE_PASSWORD=owner, APP_ROLE_PASSWORD=app) as cluster:
            self._deploy_sql(cluster, owner)

            assert int(cluster.superuser(_NOT_THE_OWNERS)) == 0
            migration = cluster.login(_OWNER, owner, (
                "CREATE TABLE budget.goals (id serial PRIMARY KEY, name text NOT NULL)"))
            assert migration.returncode == 0, migration.stderr
            written = cluster.login(_APP, app, (
                "INSERT INTO budget.goals (name) VALUES ('Rent') RETURNING name"))
            assert written.returncode == 0, written.stderr
            assert written.stdout.strip() == "Rent"
            dropped = cluster.login(_APP, app, "DROP TABLE budget.goals")
            assert dropped.returncode != 0
            assert "must be owner of table goals" in dropped.stderr

    def test_on_a_database_the_superuser_built_after_its_hand_over(
        self, tmp_path: Path,
    ) -> None:
        """A database every deploy before plan#2166 built as the superuser, once handed to the
        owner, takes the deploy's SQL as the owner, and the web login can write its tables."""
        owner, app = _password(), _password()
        with _started(tmp_path, OWNER_ROLE_PASSWORD=owner, APP_ROLE_PASSWORD=app) as cluster:
            cluster.superuser(_APP_SHAPED_SCHEMA)
            assert cluster.provision().returncode == 0

            self._deploy_sql(cluster, owner)

            written = cluster.login(_APP, app, (
                "INSERT INTO budget.accounts (name) VALUES ('Savings') RETURNING name"))
            assert written.returncode == 0, written.stderr
            assert written.stdout.strip() == "Savings"

    def test_on_a_restored_database_public_is_a_fresh_ones_again(
        self, tmp_path: Path,
    ) -> None:
        """A backup names ``public`` (``scripts/backup.sh``'s ``--schema=public``, with
        ``--clean``), so its restore drops it and makes it again, as the superuser who runs it,
        with no grant: the dump's own ``DROP SCHEMA IF EXISTS public`` and ``CREATE SCHEMA
        public`` (measured 2026-11-13, finding plan#3735).  Once handed over, ``public`` has a
        fresh database's owner and grants again, and the deploy's SQL, which creates in it,
        runs as the owner; a second run changes nothing."""
        owner, app = _password(), _password()
        with _started(tmp_path, OWNER_ROLE_PASSWORD=owner, APP_ROLE_PASSWORD=app) as cluster:
            shape = ("SELECT pg_get_userbyid(nspowner), nspacl FROM pg_namespace "
                     "WHERE nspname = 'public'")
            fresh = cluster.superuser(shape)
            cluster.superuser("DROP SCHEMA IF EXISTS public; CREATE SCHEMA public;"
                              + _APP_SHAPED_SCHEMA)
            assert cluster.superuser(shape) == f"{_SUPERUSER}|"

            ran = cluster.provision()

            assert ran.returncode == 0, ran.stderr
            # 8 as the superuser-built database's (two schemas, four tables, two functions),
            # and public: 9.
            assert "9 object(s) handed to shekel_owner" in ran.stderr
            assert cluster.superuser(shape) == fresh
            self._deploy_sql(cluster, owner)
            again = cluster.provision()
            assert again.returncode == 0, again.stderr
            assert "0 object(s) handed to shekel_owner" in again.stderr

    def test_a_database_without_the_web_login_stops_at_its_first_grant(
        self, tmp_path: Path,
    ) -> None:
        """The grants make no login: with ``shekel_app`` gone, the file stops at its first
        statement naming the missing role, as ``scripts/init_db_role.sql`` says."""
        owner, app = _password(), _password()
        with _started(tmp_path, OWNER_ROLE_PASSWORD=owner, APP_ROLE_PASSWORD=app) as cluster:
            cluster.superuser(f"DROP ROLE {_APP}")
            assert cluster.run_file(_OWNER, owner, _SCHEMAS_SQL).returncode == 0

            ran = cluster.run_file(_OWNER, owner, _GRANTS_SQL)

            assert ran.returncode != 0
            assert f'role "{_APP}" does not exist' in ran.stderr


@pytest.mark.timeout(120)
class TestTheGrantsAreOneTransaction:
    """The grants file, run as the deploy runs it, re-opens the posting ledger to the web login
    inside its own transaction alone (ruling plan#3534)."""

    def test_no_other_session_sees_the_ledger_re_opened_while_the_grants_run(
        self, tmp_path: Path,
    ) -> None:
        """Paused between its blanket grant and its revoke, the grants file has given the web
        login UPDATE and DELETE on the ledger in its own transaction alone: another session
        reads the ledger closed to it then, as before the run and after it.

        The pause is a lock.  A session holding an uncommitted grant on a ledger table's
        sequence makes the file's sequence grant, which follows its blanket table grant and
        comes before its revoke, wait for it.  The test reads the web login's privileges while
        the file waits there, then rolls the blocking grant back and lets the file finish."""
        owner, app = _password(), _password()
        with _started(tmp_path, OWNER_ROLE_PASSWORD=owner, APP_ROLE_PASSWORD=app) as cluster:
            for sql_file in (_SCHEMAS_SQL, _GRANTS_SQL):
                ran = cluster.run_file(_OWNER, owner, sql_file)
                assert ran.returncode == 0, f"{sql_file.name} as the owner: {ran.stderr}"
            made = cluster.login(_OWNER, owner, _LEDGER)
            assert made.returncode == 0, made.stderr
            # The default privileges open a table the owner makes to the web login, so the
            # probe reads a new ledger open ...
            assert cluster.superuser(_LEDGER_OPEN_TO_APP) == "t"
            # ... until the grants file's revoke closes it, the state every later deploy meets.
            ran = cluster.run_file(_OWNER, owner, _GRANTS_SQL)
            assert ran.returncode == 0, ran.stderr
            assert cluster.superuser(_LEDGER_OPEN_TO_APP) == "f"

            with _background(["exec", "-i", "-e", f"PGAPPNAME={_BLOCKER}", cluster.container,
                              "psql", "-U", _SUPERUSER, "-d", _DATABASE, "-v",
                              "ON_ERROR_STOP=1", "--no-psqlrc", "-q"],
                             stdin=subprocess.PIPE) as blocker:
                assert blocker.stdin is not None
                blocker.stdin.write("BEGIN;\nGRANT SELECT ON SEQUENCE "
                                    f"budget.journal_entries_id_seq TO {_APP};\n")
                blocker.stdin.flush()
                blocking = _await_row(cluster, (
                    "SELECT pid FROM pg_stat_activity "
                    f"WHERE application_name = '{_BLOCKER}' AND state = 'idle in transaction'"
                ), "the blocking grant held open")
                with (_GRANTS_SQL.open(encoding="utf-8") as source,
                      _background(cluster.file_command(_OWNER, owner, _GRANTS_SQL),
                                  stdin=source) as grants):
                    _await_row(cluster, (
                        "SELECT pid FROM pg_stat_activity "
                        f"WHERE usename = '{_OWNER}' AND query LIKE 'GRANT USAGE%SEQUENCES%' "
                        f"AND pg_blocking_pids(pid) @> ARRAY[{blocking}]"
                    ), "the grants file waiting at its sequence grant")

                    assert cluster.superuser(_LEDGER_OPEN_TO_APP) == "f", (
                        "another session read UPDATE or DELETE on the ledger granted to "
                        f"{_APP} while the grants file ran: the file is not one transaction, "
                        "and a serving app could edit the ledger between its blanket GRANT "
                        "and its REVOKE"
                    )

                    blocker.communicate("ROLLBACK;\n", timeout=_DOCKER_TIMEOUT_S)
                    _, error = grants.communicate(timeout=_DOCKER_TIMEOUT_S)
                    assert grants.returncode == 0, error
            assert blocker.returncode == 0
            assert cluster.superuser(_LEDGER_OPEN_TO_APP) == "f"
