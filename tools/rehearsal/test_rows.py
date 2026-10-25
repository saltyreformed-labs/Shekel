"""The rows (step X-cx's K1b-1): each side's deploy step in an environment built whole, every
row of every table compared, the noise gate, the planted controls and the verdict.

The cases marked ``cluster`` start a real private cluster through a :class:`bench.Bench` on the
conftest's ``dump``; the last of them deploy both sides by production's own step, so they also
need each side's venv (cached after its first build).  The rest grade the pure parts on planted
inputs.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Callable, Iterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import psycopg
import pytest

from tools.rehearsal import bench, rows
from tools.rehearsal.rows import Held, Moved


def test_a_table_is_compared_by_its_key_only_when_both_sides_agree_on_one():
    """The key both sides hold; a multiset where they differ, where there is none, and for
    the audit log always; a table on one side by that side's key."""
    before = {("budget", "a"): Held(("id",)), ("budget", "b"): Held(("id",)),
              ("budget", "c"): Held(()), ("system", "audit_log"): Held(("id",)),
              ("budget", "d"): Held(("id", "part"))}
    after = {("budget", "a"): Held(("id",)), ("budget", "b"): Held(("id", "kind")),
             ("budget", "c"): Held(()), ("system", "audit_log"): Held(("id",)),
             ("budget", "e"): Held(("code",))}
    assert rows.keys(before, after) == {
        ("budget", "a"): ("id",), ("budget", "b"): (), ("budget", "c"): (),
        ("budget", "d"): ("id", "part"), ("budget", "e"): ("code",),
        ("system", "audit_log"): ()}


def test_a_column_is_a_stamp_only_where_every_side_holding_its_table_says_so():
    """Both sides' stamps; a column one side's catalog calls a stamp and the other's does
    not (a cut adding or dropping a clock default) is none, so it is compared exactly; a
    table on one side by that side's stamps.  Every day of either side is named."""
    before = {("budget", "a"): Held(("id",), ("created_at", "updated_at")),
              ("budget", "b"): Held(("id",), ("created_at",), ("on_day",)),
              ("budget", "d"): Held(("id",), ("created_at",))}
    after = {("budget", "a"): Held(("id",), ("created_at", "updated_at")),
             ("budget", "b"): Held(("id",), ("created_at", "posted_at")),
             ("budget", "e"): Held(("id",), ("seen_at",), ("due_on",))}
    assert rows.stamps(before, after) == {
        ("budget", "a"): ("created_at", "updated_at"), ("budget", "b"): ("created_at",),
        ("budget", "d"): ("created_at",), ("budget", "e"): ("seen_at",)}
    one, other = after, before
    assert rows.stamps(one, other)[("budget", "b")] == ("created_at",)
    assert rows.days(before, after) == ["budget.b.on_day", "budget.e.due_on"]


_CHANGED = Moved("budget.accounts", "[3]", {"id": 3, "balance": Decimal("10.00"), "name": "A",
                                            "active": True},
                 {"id": 3, "balance": Decimal("12.50"), "name": "B", "active": False})
_MOVED = [
    _CHANGED,
    Moved("budget.accounts", "[4]", {"id": 4, "balance": Decimal("1.00"), "name": "C",
                                     "active": True},
          {"id": 4, "balance": Decimal("0.75"), "name": "C", "active": True}),
    Moved("budget.accounts", "[5]", {"id": 5, "balance": None, "name": "D", "active": True},
          {"id": 5, "balance": Decimal("2.00"), "name": "D", "active": True}),
    Moved("budget.accounts", "[6]", {"id": 6}, None),
    Moved("system.audit_log", "", None, ["budget", "accounts", "UPDATE", 3]),
]


def test_the_summary_sums_each_table_and_column_and_totals_only_numbers():
    """Rows per table by side; per column the rows, and a total only where every changed
    value is a number on both sides (a boolean is not one, nor a null)."""
    assert rows.summary(_MOVED, "base", "cut") == [
        "budget.accounts: 4 rows differ (1 only on base, 0 only on cut, 3 changed)",
        "  active: 1 rows",
        "  balance: 3 rows",
        "  name: 1 rows",
        "system.audit_log: 1 rows differ (0 only on base, 1 only on cut, 0 changed)",
    ]
    assert rows.summary(_MOVED[:2], "base", "cut")[2] == "  balance: 2 rows, total change +2.25"


def test_the_report_names_each_row_and_each_changed_column():
    """One line per row: a changed row's columns before and after, a row on one side whole."""
    assert list(rows.report(_MOVED[:1] + _MOVED[3:], "base", "cut")) == [
        'budget.accounts [3]: active true -> false; balance "10.00" -> "12.50"; '
        'name "A" -> "B"',
        'budget.accounts [6]: only on base: {"id": 6}',
        'system.audit_log (no key): only on cut: ["budget", "accounts", "UPDATE", 3]',
    ]


def test_a_control_is_found_only_when_it_is_the_one_row_and_column_found():
    """Exactly the planted row and column; a second row, another key or column, or a
    removal read as a change, is not found."""
    planted = ("budget.accounts", "[3]", "name")
    assert rows.found([_CHANGED], planted) is False
    one = Moved("budget.accounts", "[3]", {"id": 3, "name": "A"}, {"id": 3, "name": "B"})
    assert rows.found([one], planted) is True
    assert rows.found([one, _MOVED[3]], planted) is False
    assert rows.found([one], ("budget.accounts", "[4]", "name")) is False
    assert rows.found([one], planted, removed=True) is False
    gone = Moved("budget.accounts", "[6]", {"id": 6}, None)
    assert rows.found([gone], ("budget.accounts", "[6]", ""), removed=True) is True


def _side(root: Path, dockerfile: str, compose: str) -> Path:
    """A planted side's tree: its Dockerfile and production compose file."""
    (root / "deploy").mkdir(parents=True)
    (root / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    (root / "deploy" / "docker-compose.prod.yml").write_text(compose, encoding="utf-8")
    return root


_DOCKERFILE = ('FROM python\nENV PATH="/opt/venv/bin:$PATH"\nRUN true\nENV PSYCOPG_IMPL=c\n'
               "ENV LC_ALL=C.UTF-8\n")
_PROD_COMPOSE = "services:\n  app:\n    environment:\n      TZ: America/New_York\n"


def _state(worktree: Path, python: Path) -> bench.State:
    """A state over a planted tree and interpreter."""
    return bench.State("cut", "0" * 40, worktree, "rehearsal_cut", "postgresql:///x", python)


#: The instant a planted deploy is pinned at: noon in New York, the planted compose file's zone.
_MOMENT = datetime(2026, 10, 20, 12, tzinfo=ZoneInfo("America/New_York"))
#: The side probe a deploy runs, by path.
_PROBE = Path(rows.__file__).resolve().parent / "side" / "deploy.py"


def test_the_side_environment_is_built_whole_from_the_side_s_tree(tmp_path, monkeypatch):
    """Production's variables from the side's own Dockerfile (not PATH) and compose file, the
    configuration, URL, key, store and seed, and the side's venv first: nothing else, so a
    variable of the shell that runs the kit never reaches the side."""
    monkeypatch.setenv("DATABASE_URL_APP", "postgresql:///operator")
    monkeypatch.setenv("SHEKEL_FAKE_TODAY", "2026-01-01")
    tree = _side(tmp_path / "tree", _DOCKERFILE, _PROD_COMPOSE)
    built = rows.side_environment(_state(tree, tmp_path / "venv" / "bin" / "python"),
                                    "postgresql:///copy", "k" * 64)
    assert built == {
        "PSYCOPG_IMPL": "c", "LC_ALL": "C.UTF-8", "TZ": "America/New_York",
        "FLASK_ENV": "production", "SECRET_KEY": "k" * 64,
        "DATABASE_URL": "postgresql:///copy",
        "RATELIMIT_STORAGE_URI": "redis://rehearsal.invalid:6379/0", "PYTHONHASHSEED": "0",
        "PYTHON_DOTENV_DISABLED": "1",
        "PATH": f"{tmp_path / 'venv' / 'bin'}{os.pathsep}/usr/bin{os.pathsep}/bin"}


@pytest.mark.parametrize("compose", ["services: {}\n", _PROD_COMPOSE + "      TZ: UTC\n"])
def test_a_side_whose_compose_file_pins_no_single_timezone_is_refused(tmp_path, compose):
    """No ``TZ``, or two, is refused by name rather than guessed."""
    tree = _side(tmp_path, _DOCKERFILE, compose)
    with pytest.raises(bench.BenchError, match=r"docker-compose\.prod\.yml pins [02] TZ lines"):
        rows.image_environment(tree)


_RECORDER = """#!{python}
import json, os, sys
with open("seen.json", "w", encoding="utf-8") as seen:
    json.dump({{"argv": sys.argv[1:], "cwd": os.getcwd(), "env": dict(os.environ)}}, seen)
print("Database initialised.")
print("the planted refusal", file=sys.stderr)
sys.exit({status})
"""


@pytest.mark.parametrize("status", [0, 3])
def test_a_deploy_runs_the_side_s_step_from_its_tree_in_that_environment_alone(tmp_path,
                                                                            status):
    """The side's interpreter runs the side probe ``side/deploy.py`` with the pinned instant,
    from the side's worktree in exactly :func:`rows.side_environment`; exit 0 is None, else
    its status and stderr."""
    tree = _side(tmp_path / "tree", _DOCKERFILE, _PROD_COMPOSE)
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(_RECORDER.format(python=sys.executable, status=status), encoding="utf-8")
    python.chmod(0o755)
    state = _state(tree, python)
    said = rows.deploy(state, "postgresql:///copy", "k" * 64, _MOMENT)
    assert said == (None if status == 0 else "exit 3:\nthe planted refusal")
    seen = json.loads((tree / "seen.json").read_text(encoding="utf-8"))
    assert seen["argv"] == [str(_PROBE), "2026-10-20T12:00:00-04:00"]
    assert seen["cwd"] == str(tree)
    assert seen["env"] == rows.side_environment(state, "postgresql:///copy", "k" * 64)


_PLANTED_STEP = """import datetime, json, sys, time
with open("seen.json", "w", encoding="utf-8") as seen:
    json.dump({"now": datetime.datetime.now().isoformat(),
               "today": datetime.date.today().isoformat(), "time": time.time(),
               "argv": sys.argv, "path": sys.path[0], "name": __name__, "file": __file__}, seen)
"""


def test_the_side_s_step_runs_as_the_image_runs_it_with_its_clock_pinned(tmp_path):
    """``side/deploy.py`` runs the tree's ``scripts/init_database.py`` as ``__main__``, its
    ``__file__`` and only argument its absolute path, ``sys.path[0]`` the tree's ``scripts/``
    (never the kit's ``side/``), and every wall clock frozen at the instant: local time noon
    in the zone the side's compose file pins."""
    tree = _side(tmp_path / "tree", _DOCKERFILE, _PROD_COMPOSE)
    (tree / "scripts").mkdir()
    step = tree / "scripts" / "init_database.py"
    step.write_text(_PLANTED_STEP, encoding="utf-8")
    assert rows.deploy(_state(tree, Path(sys.executable)), "postgresql:///copy", "k" * 64,
                       _MOMENT) is None
    assert json.loads((tree / "seen.json").read_text(encoding="utf-8")) == {
        "now": "2026-10-20T12:00:00", "today": "2026-10-20", "time": _MOMENT.timestamp(),
        "argv": [str(step)], "path": str(tree / "scripts"), "name": "__main__",
        "file": str(step)}


_DOTENV_STEP = """import json, os
from dotenv import load_dotenv
loaded = load_dotenv()
with open("seen.json", "w", encoding="utf-8") as seen:
    json.dump({"loaded": loaded, "url": os.environ.get("DATABASE_URL_APP")}, seen)
"""


def test_no_dotenv_file_reaches_a_side(tmp_path):
    """A ``.env`` in the side's tree, where ``load_dotenv()`` finds it from the step's own
    directory, sets nothing: the side reads only the environment built whole, as production,
    whose image holds no ``.env``."""
    tree = _side(tmp_path / "tree", _DOCKERFILE, _PROD_COMPOSE)
    (tree / ".env").write_text("DATABASE_URL_APP=postgresql:///elsewhere\n", encoding="utf-8")
    (tree / "scripts").mkdir()
    (tree / "scripts" / "init_database.py").write_text(_DOTENV_STEP, encoding="utf-8")
    assert rows.deploy(_state(tree, Path(sys.executable)), "postgresql:///copy", "k" * 64,
                       _MOMENT) is None
    assert json.loads((tree / "seen.json").read_text(encoding="utf-8")) == {
        "loaded": False, "url": None}


def test_the_pinned_instant_is_noon_of_the_dump_s_day_in_base_s_zone(tmp_path):
    """A dump made at 03:30 UTC on the 20th was made on the 19th in New York, the zone base's
    compose file pins: every side's clock is pinned at noon there on the 19th."""
    tree = _side(tmp_path / "tree", _DOCKERFILE, _PROD_COMPOSE)
    made = SimpleNamespace(
        made={"base": _state(tree, tmp_path / "python")},
        listing=bench.Listing(18, datetime(2026, 10, 20, 3, 30, tzinfo=ZoneInfo("UTC"))))
    assert rows.pinned_instant(made).isoformat() == "2026-10-19T12:00:00-04:00"


#: A planted ``app`` package: what ``side/boot.py`` imports, recording what it was given and
#: read, and when, into ``seen.json`` once the query is answered.
_PLANTED_APP = {
    "__init__.py": """import contextlib, datetime, sys
seen = {"imported": datetime.datetime.now().isoformat()}


class _App:
    @contextlib.contextmanager
    def app_context(self):
        seen["context"] = True
        yield
        seen["context"] = False


def create_app(**given):
    seen.update(given=given, now=datetime.datetime.now().isoformat(), path=sys.path[0])
    return _App()
""",
    "extensions.py": """import json
from app import seen


class _Answer:
    def scalar_one(self):
        with open("seen.json", "w", encoding="utf-8") as out:
            json.dump(seen, out)
        return 0


class _Session:
    def execute(self, statement):
        seen.update(query=statement, in_context=seen.get("context", False))
        return _Answer()


class _Db:
    session = _Session()

    def text(self, query):
        return "text: " + query


db = _Db()
""",
}


def test_the_boot_imports_the_side_s_app_and_boots_it_as_gunicorn_does_under_the_pin(tmp_path):
    """``side/boot.py`` imports the tree's own ``app`` (``sys.path[0]`` the tree, never the
    kit's ``side/``) with the clock already pinned, calls ``create_app()`` with nothing (the
    reference cache loaded eagerly, as ``run.py`` has it), and answers its one query inside
    the app's context."""
    tree = _side(tmp_path / "tree", _DOCKERFILE, _PROD_COMPOSE)
    (tree / "app").mkdir()
    for name, source in _PLANTED_APP.items():
        (tree / "app" / name).write_text(source, encoding="utf-8")
    assert rows.boot(_state(tree, Path(sys.executable)), "postgresql:///copy", "k" * 64,
                     _MOMENT) is None
    assert json.loads((tree / "seen.json").read_text(encoding="utf-8")) == {
        "imported": "2026-10-20T12:00:00", "given": {}, "now": "2026-10-20T12:00:00",
        "path": str(tree), "context": True,
        "query": "text: SELECT count(*) FROM salary.pay_stubs", "in_context": True}


def test_a_revision_resolves_only_by_a_migration_script_named_for_it(tmp_path):
    """As ``shekel-deploy.sh``'s ``image_resolves_revisions`` reads the image's listing: a
    ``<revision>_*.py`` file for every revision, matched up to its ``_`` (a revision that is
    another's prefix does not resolve), a file not ``.py`` is not a script, and none to
    resolve resolves."""
    versions = tmp_path / "migrations" / "versions"
    versions.mkdir(parents=True)
    for name in ("abc123_add_x.py", "def456_y.py", "fed789_z.txt"):
        (versions / name).write_text("", encoding="utf-8")
    assert rows.base_resolves(tmp_path, ["abc123"]) is True
    assert rows.base_resolves(tmp_path, ["abc123", "def456"]) is True
    assert rows.base_resolves(tmp_path, []) is True
    assert rows.base_resolves(tmp_path, ["abc12"]) is False
    assert rows.base_resolves(tmp_path, ["fed789"]) is False
    assert rows.base_resolves(tmp_path, ["abc123", "0ff0ff"]) is False


class _Failing:
    """A bench whose making fails as ``error`` does: a file or the database, not the kit."""

    def __init__(self, error: Exception) -> None:
        """Fail with ``error``."""
        self.error = error

    def __call__(self, *_given: object) -> _Failing:
        """Stand in for :class:`bench.Bench`'s constructor."""
        return self

    def __enter__(self) -> bench.Bench:
        """Fail as the making would."""
        raise self.error

    def __exit__(self, *_given: object) -> None:
        """Nothing was made."""


@pytest.mark.parametrize("error", [OSError(2, "No such file or directory"),
                                   psycopg.OperationalError("the cluster went away")])
def test_a_run_that_fails_itself_exits_2_never_the_1_that_says_rows_differ(monkeypatch,
                                                                         capsys, error):
    """A file the run cannot write or a database error ends it measuring nothing (exit 2),
    named, where Python's own exit status for it would be 1, the verdict that rows differ."""
    monkeypatch.setattr(bench, "Bench", _Failing(error))
    assert rows.main(["--dump", "d", "--base", "b", "--cut", "c", "--report", "r"]) == 2
    assert capsys.readouterr().err == f"[rehearsal] {error}\n"


_PLANTED_SCHEMA = """
CREATE TABLE system.audit_log (
    id bigserial PRIMARY KEY, table_schema varchar(50) NOT NULL, table_name varchar(100) NOT NULL,
    operation varchar(10) NOT NULL, row_id integer, old_data jsonb, new_data jsonb,
    changed_fields text[], user_id integer, db_user varchar(100) DEFAULT current_user,
    executed_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE note (id integer, part integer, body varchar(4) NOT NULL,
                   created_at timestamptz DEFAULT clock_timestamp(), PRIMARY KEY (id, part));
CREATE TABLE tally (word text);
CREATE TABLE timing (id integer PRIMARY KEY, due timestamptz DEFAULT now() + interval '1 day',
                     at_local timestamp DEFAULT LOCALTIMESTAMP(3),
                     at_now timestamptz DEFAULT CURRENT_TIMESTAMP,
                     on_day date DEFAULT CURRENT_DATE);
INSERT INTO note (id, part, body) VALUES (1, 1, 'abc'), (1, 2, 'de'), (2, 1, 'f');
INSERT INTO tally VALUES ('x'), ('x'), ('y');
INSERT INTO system.audit_log (table_schema, table_name, operation, row_id, new_data)
    VALUES ('public', 'note', 'INSERT', 1, '{"id": 1, "created_at": "2000-01-01T00:00:00+00:00"}');
CREATE FUNCTION audit() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO system.audit_log (table_schema, table_name, operation, row_id)
        VALUES (TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP, 0);
    RETURN NULL;
END $$;
CREATE TRIGGER audit AFTER INSERT OR UPDATE OR DELETE ON ledger
    FOR EACH ROW EXECUTE FUNCTION audit();
CREATE TRIGGER audit AFTER INSERT OR UPDATE OR DELETE ON note
    FOR EACH ROW EXECUTE FUNCTION audit();
"""


@pytest.fixture(name="planted")
def _planted(dump: Path) -> Iterator[bench.Bench]:
    """A bench whose base copy holds the dump (its schemas, its ledger, its stamped row) and
    the planted tables: a composite key with a stamp, a multiset, a table of clock defaults,
    and an audit log a trigger writes."""
    made = bench.Bench(dump, {"base": "HEAD"})
    with made:
        with psycopg.connect(made.made["base"].url, autocommit=True) as connection:
            connection.execute(_PLANTED_SCHEMA)
        yield made


def _clock(url: str) -> datetime:
    """The cluster's clock, read on the copy at ``url``."""
    with psycopg.connect(url) as connection:
        (now,) = connection.execute("SELECT clock_timestamp()").fetchone()
    return now


#: A line before every stamp the tests write, so each reads as written by a deploy.
_LONG_AGO = datetime(2000, 1, 1, tzinfo=ZoneInfo("UTC"))
#: A line after every stamp the tests write, so each reads exactly.
_FAR_AHEAD = datetime(2100, 1, 1, tzinfo=ZoneInfo("UTC"))


@pytest.mark.cluster
def test_a_stamp_is_a_timestamp_whose_default_is_a_clock_read_and_nothing_else(planted):
    """From the catalog: ``clock_timestamp()``, ``CURRENT_TIMESTAMP``, ``LOCALTIMESTAMP(3)``
    and the audit log's ``now()`` make stamps, ``now()`` plus an interval does not, and a
    date the server's day fills is a day."""
    with psycopg.connect(planted.made["base"].url) as connection:
        held = rows.catalog(connection)
    assert held[("public", "note")] == Held(("id", "part"), ("created_at",))
    assert held[("public", "timing")] == Held(("id",), ("at_local", "at_now"), ("on_day",))
    assert held[("system", "audit_log")] == Held(("id",), ("executed_at",))
    assert held[("public", "tally")] == Held(())


@pytest.mark.cluster
def test_a_stamp_later_than_the_line_reads_as_deploy_and_every_other_is_compared(planted):
    """R-BAL331: two copies whose stamps the clock re-wrote after the line, one after the
    other (the audit log's own clock and the stamps of the rows its events wrote with them),
    read the same, the audit log's id and database user still left out; read through a line
    later than both, the same stamps differ.  A stamp moved earlier, a changed value, a row
    more in a multiset and the audit event the change's trigger wrote are each found."""
    base = planted.made["base"]
    line = _clock(base.url)
    one = planted.clone(base.database, "one")
    other = planted.clone(base.database, "other")
    for url, also in ((one, ""), (other, ", id = id + 100, db_user = 'someone'")):
        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute("SET session_replication_role = replica")
            connection.execute("UPDATE note SET created_at = now()")
            connection.execute("UPDATE system.audit_log SET executed_at = now(), new_data = "
                               "new_data || jsonb_build_object('created_at', now())" + also)
    assert not rows.compare(one, other, line)
    assert {row.table for row in rows.compare(one, other, _clock(base.url))} == {
        "public.note", "system.audit_log"}
    with psycopg.connect(other, autocommit=True) as connection:
        connection.execute("UPDATE note SET body = 'dx' WHERE id = 1 AND part = 2")
        connection.execute("INSERT INTO tally VALUES ('x')")
        connection.execute("SET session_replication_role = replica")
        connection.execute("UPDATE note SET created_at = '2000-01-01T00:00:00+00:00' "
                           "WHERE id = 2")
    deployed = rows.DEPLOY
    assert rows.compare(one, other, line) == [
        Moved("public.note", "[1, 2]", {"id": 1, "part": 2, "body": "de", "created_at": deployed},
              {"id": 1, "part": 2, "body": "dx", "created_at": deployed}),
        Moved("public.note", "[2, 1]", {"id": 2, "part": 1, "body": "f", "created_at": deployed},
              {"id": 2, "part": 1, "body": "f", "created_at": "2000-01-01T00:00:00+00:00"}),
        Moved("public.tally", "", None, {"word": "x"}),
        Moved("system.audit_log", "", None, ["public", "note", "UPDATE", 0, None, None, None,
                                              None, {"executed_at": deployed}]),
    ]
    assert rows.multisets(one, other) == ["public.tally", "system.audit_log"]


@pytest.mark.cluster
def test_a_dump_holding_a_stamp_later_than_the_line_measures_nothing(planted):
    """The line is read on a copy every stamp of which is earlier; a row's stamp, the audit
    log's own clock and a stamp of a row an event wrote, each dated ahead of it, refuse the
    run, each named."""
    url = planted.made["base"].url
    assert rows.read_line(url) < _clock(url)
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute("SET session_replication_role = replica")
        connection.execute("UPDATE note SET created_at = now() + interval '1 day' WHERE id = 2")
        connection.execute("UPDATE system.audit_log SET executed_at = now() + interval '1 day', "
                           "new_data = new_data || "
                           "jsonb_build_object('created_at', now() + interval '1 day')")
    with pytest.raises(bench.BenchError) as refused:
        rows.read_line(url)
    assert str(refused.value).startswith("the dump holds stamps later than the cluster's clock")
    assert str(refused.value).endswith(
        "cannot be told from them: public.note.created_at, system.audit_log.executed_at, "
        "system.audit_log.new_data.created_at")


@pytest.mark.cluster
def test_each_control_is_planted_in_one_row_and_found_exactly(planted):
    """The cent in the first money column, the byte in the first text column (its length
    kept under a length limit), the removal of the first keyed row, and the first stamp moved
    a microsecond before both itself and the line, whether the line is after it or before it
    (a stamp a deploy wrote): each moves its one row, no trigger writing an audit event, and
    the comparison finds it and nothing else."""
    base = planted.made["base"]
    line = _clock(base.url)
    for name, plant, removed, expected in (
            ("cent", rows.plant_cent, False, ("public.ledger", "[1]", "amount")),
            ("byte", rows.plant_byte, False, ("public.note", "[1, 1]", "body")),
            ("removal", rows.plant_removal, True, ("public.ledger", "[1]", ""))):
        url = planted.clone(base.database, name)
        assert plant(url) == expected
        moved = rows.compare(base.url, url, line)
        assert rows.found(moved, expected, removed), moved
    assert moved[0].before == {"id": 1, "amount": Decimal("12.34")}
    assert not rows.compare(base.url, planted.clone(base.database, "untouched"), line)
    for name, at in (("earlier", line), ("earlier_than_a_deploy_s", _LONG_AGO)):
        url = planted.clone(base.database, name)
        planted_at = rows.plant_earlier(url, at)
        assert planted_at == ("public.note", "[1, 1]", "created_at")
        moved = rows.compare(base.url, url, at)
        assert rows.found(moved, planted_at), (name, moved)


@pytest.mark.cluster
@pytest.mark.parametrize("case", ["read alike", "not later than the line", "the same value"])
def test_a_stamp_the_clock_wrote_on_two_clones_in_turn_is_not_found(planted, monkeypatch,
                                                                     case):
    """The fifth control: two values the clock wrote, each later than the line, read alike.
    Two values no later than the line, or one value on both, refuse, since the control would
    then measure nothing."""
    base = planted.made["base"]
    line = {"read alike": _clock(base.url), "not later than the line": _FAR_AHEAD,
            "the same value": _LONG_AGO}[case]
    if case == "the same value":
        monkeypatch.setattr(rows, "plant_now", lambda url: rows.plant_earlier(url, _FAR_AHEAD))
    wrong = rows.rewritten_twice(planted, base, line)
    if case == "read alike":
        assert wrong is None
    else:
        assert wrong is not None and wrong.startswith(
            "a stamp the clock wrote on two clones in turn planted at public.note [1, 1] "
            "created_at and public.note [1, 1] created_at wrote ")
        assert wrong.endswith(f"not two moments each later than the line {line.isoformat()}, "
                              "so it measures nothing")


@pytest.mark.cluster
def test_every_control_runs_and_a_wrong_one_is_named(planted, monkeypatch):
    """The four found exactly pass on the planted copy, and what the fifth says is wrong is
    named among what is wrong."""
    base = planted.made["base"]
    monkeypatch.setattr(rows, "rewritten_twice", lambda *_given: "the fifth, planted")
    assert rows.controls(planted, base, _clock(base.url)) == ["the fifth, planted"]


@pytest.mark.cluster
def test_a_control_no_row_can_take_is_refused_by_name(planted):
    """A copy with no text column outside a key refuses the byte by name."""
    base = planted.made["base"]
    url = planted.clone(base.database, "bare")
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute("DROP TABLE note; DROP TABLE tally; DROP SCHEMA system CASCADE")
    with pytest.raises(bench.BenchError, match=r"no row of the copy could take a control in "
                                               r"a column of \['text', 'character varying'\]"):
        rows.plant_byte(url)


def _rehearsal(dump: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
               ) -> tuple[int, str, str, str]:
    """``python -m tools.rehearsal.rows`` on the dump, base and cut both HEAD: its status,
    stdout, stderr and report."""
    out = tmp_path / "report.txt"
    status = rows.main(["--dump", str(dump), "--base", "HEAD", "--cut", "HEAD",
                        "--report", str(out)])
    said = capsys.readouterr()
    return status, said.out, said.err, out.read_text(encoding="utf-8") if out.exists() else ""


def _planting(database: str, plant: Callable[[str], object]) -> Callable[..., str | None]:
    """The deploy step, then ``plant`` on the copy, for the one deploy of ``database``."""
    deploy = rows.deploy

    def deployed(state: bench.State, url: str, secret_key: str, moment: datetime
                 ) -> str | None:
        said = deploy(state, url, secret_key, moment)
        if state.database == database and said is None:
            plant(url)
        return said
    return deployed


def _executing(statement: str) -> Callable[[str], None]:
    """A plant that runs ``statement`` on the copy, triggers and foreign-key checks off."""
    def run(url: str) -> None:
        with psycopg.connect(url, autocommit=True) as connection:
            connection.execute("SET session_replication_role = replica")
            connection.execute(statement)
    return run


def _refusing(database: str) -> Callable[..., str | None]:
    """The deploy step, except for ``database``'s, which refuses having written nothing, as
    the step's one transaction leaves a refused copy."""
    deploy = rows.deploy

    def deployed(state: bench.State, url: str, secret_key: str, moment: datetime
                 ) -> str | None:
        if state.database == database:
            return "exit 1:\nplanted"
        return deploy(state, url, secret_key, moment)
    return deployed


#: What a run whose every control read as planted prints of them.
_CONTROLS_LINE = ("controls: a cent, a byte, a removed row, a stamp moved earlier, each found "
                  "exactly; a stamp the clock wrote on two clones in turn, not found\n")


@pytest.mark.cluster
def test_two_sides_that_store_the_same_rows_exit_0_saying_what_was_not_compared(
        dump, tmp_path, capsys):
    """Each side deployed by production's step on the dump (a first deploy, here), base
    twice the same, every control found: exit 0, and the stated limits printed."""
    status, out, err, report = _rehearsal(dump, tmp_path, capsys)
    assert (status, err, report) == (0, "", "")
    assert re.search(r"^clock pinned at \d{4}-\d{2}-\d{2}T12:00:00-0[45]:00$", out, re.MULTILINE)
    assert re.search(r"^line: \d{4}-\d{2}-\d{2}T[0-9:.]+\+00:00, the cluster's clock just before "
                     r"the first deploy; a stamp later than it reads as 'deploy'$", out,
                     re.MULTILINE)
    assert "deploy base: " in out and "deploy base, again: " in out and "deploy cut: " in out
    assert "noise gate: " in out and ", 0 rows differ\n" in out
    assert _CONTROLS_LINE in out
    assert rows.NOT_COMPARED + "\n" in out
    assert ("compared, though the server's clock fills them: the days "
            "budget.transaction_entries.purchased_on, each the server's real day where a "
            "deploy inserts a row without one, so deploys either side of midnight in the "
            "server's zone differ by it (a false difference, never a false pass)\n") in out
    assert "compared as multisets: system.audit_log\n" in out
    assert "base against cut: " in out and "teardown: " in out
    assert re.search(r"^rollback: base's image knows the migration version cut's copy is "
                     r"stamped at \([0-9a-z]+\); base boots on cut's deployed copy, and base's "
                     r"deploy step, run again on it, exits 0 and moves no row$", out, re.MULTILINE)


@pytest.mark.cluster
def test_a_row_cut_s_deploy_stores_differently_exits_1_naming_it(dump, tmp_path, capsys,
                                                                 monkeypatch):
    """A cent cut's copy holds that base's does not is the one row differing: named in the
    report, summed on stdout, exit 1."""
    planted: list[tuple[str, str, str]] = []
    monkeypatch.setattr(rows, "deploy", _planting(
        "rehearsal_cut", lambda url: planted.append(rows.plant_cent(url))))
    status, out, _, report = _rehearsal(dump, tmp_path, capsys)
    table, key, column = planted[0]
    assert status == 1
    assert report.startswith(f"{table} {key}: {column} ") and report.count("\n") == 1
    assert f"{table}: 1 rows differ (0 only on base, 0 only on cut, 1 changed)\n" \
        f"  {column}: 1 rows, total change +0.01\n" in out


@pytest.mark.cluster
def test_a_stamp_of_the_dump_cut_s_deploy_moves_earlier_exits_1_naming_it(dump, tmp_path,
                                                                          capsys, monkeypatch):
    """R-BAL331's own case: cut's deploy moves a stamp the dump holds a second earlier (a tie
    it breaks would flip); stamps are compared, so it is the one row differing, named in the
    report, summed on stdout, exit 1."""
    monkeypatch.setattr(rows, "deploy", _planting("rehearsal_cut", _executing(
        "UPDATE stamped SET created_at = created_at - interval '1 second'")))
    status, out, _, report = _rehearsal(dump, tmp_path, capsys)
    assert status == 1
    assert report.startswith("public.stamped [1]: created_at ") and report.count("\n") == 1
    assert ("public.stamped: 1 rows differ (0 only on base, 0 only on cut, 1 changed)\n"
            "  created_at: 1 rows\n") in out


@pytest.mark.cluster
def test_stamps_each_deploy_writes_at_its_own_moment_read_alike(dump, tmp_path, capsys,
                                                                monkeypatch):
    """Every deploy, base's twice, cut's and the rollback's, writing the server's clock into
    the dump's stamp at its own moment: each stamp reads as a deploy's, so base deployed
    twice reads the same, nothing differs and the rollback moves no row, exit 0."""
    deploy = rows.deploy
    stamp = _executing("UPDATE stamped SET created_at = now()")

    def deployed(state: bench.State, url: str, secret_key: str, moment: datetime
                 ) -> str | None:
        said = deploy(state, url, secret_key, moment)
        if said is None:
            stamp(url)
        return said
    monkeypatch.setattr(rows, "deploy", deployed)
    status, out, err, report = _rehearsal(dump, tmp_path, capsys)
    assert (status, err, report) == (0, "", "")
    assert "noise gate: " in out and ", 0 rows differ\n" in out
    assert "base against cut: " in out and ", 0 rows differ (each in " in out


@pytest.mark.cluster
def test_base_deployed_twice_reading_differently_measures_nothing(dump, tmp_path, capsys,
                                                                  monkeypatch):
    """A byte planted in base's second copy turns the noise gate: exit 2, named, no verdict."""
    monkeypatch.setattr(rows, "deploy", _planting("rehearsal_base_again", rows.plant_byte))
    status, out, err, report = _rehearsal(dump, tmp_path, capsys)
    assert (status, report) == (2, "")
    assert "noise gate: " in out and ", 1 rows differ\n" in out
    assert err.startswith("[rehearsal] base deployed twice did not read the same")
    assert "1 rows differ (0 only on base, 0 only on base again, 1 changed)" in err


@pytest.mark.cluster
def test_base_s_deploy_refusing_the_dump_measures_nothing(dump, tmp_path, capsys, monkeypatch):
    """Base's deploy refusing production's data leaves nothing to compare: exit 2, the
    refusal quoted."""
    monkeypatch.setattr(rows, "deploy", _refusing("rehearsal_base"))
    status, _, err, report = _rehearsal(dump, tmp_path, capsys)
    assert (status, report) == (2, "")
    assert err.startswith("[rehearsal] base's deploy refused the dump, so nothing is compared "
                          "(an app defect): exit 1:\nplanted")


@pytest.mark.cluster
def test_cut_s_deploy_refusing_what_base_s_took_is_a_difference(dump, tmp_path, capsys,
                                                                monkeypatch):
    """Cut's deploy refusing the copy base's deployed is a difference: exit 1, the refusal
    quoted, after the gates ran."""
    monkeypatch.setattr(rows, "deploy", _refusing("rehearsal_cut"))
    status, out, _, report = _rehearsal(dump, tmp_path, capsys)
    assert (status, report) == (1, "")
    assert _CONTROLS_LINE in out
    assert "cut's deploy refused the copy base's deployed: exit 1:\nplanted\n" in out


_AGAIN = "ROLLBACK FAILS: base's deploy step, run again on cut's deployed copy,"


@pytest.mark.cluster
def test_a_copy_stamped_where_base_s_tree_has_no_script_is_a_restore(dump, tmp_path, capsys,
                                                                     monkeypatch):
    """Cut's copy stamped at a revision base's tree carries no script for: shekel-deploy.sh
    would refuse the rollback, so the run says rolling back is a restore and runs neither the
    step nor the boot; the stamp is a row that differs, so exit 1."""
    monkeypatch.setattr(rows, "deploy", _planting("rehearsal_cut", _executing(
        "UPDATE public.alembic_version SET version_num = 'feedface0000'")))
    status, out, _, report = _rehearsal(dump, tmp_path, capsys)
    assert status == 1 and "public.alembic_version" in report
    assert ("rollback: base's image does not know the migration version cut's copy is stamped "
            "at (feedface0000), so shekel-deploy.sh would refuse the rollback and name the dump "
            "to restore: rolling back past this release is a restore\n") in out
    assert "ROLLBACK FAILS" not in out


@pytest.mark.cluster
def test_base_s_step_refusing_cut_s_copy_fails_the_rollback_and_nothing_boots(
        dump, tmp_path, capsys, monkeypatch):
    """Base's deploy step, run again on cut's deployed copy, refusing it: production's
    container dies at step 3, so the rollback fails, quoted, exit 1, though no row differs."""
    monkeypatch.setattr(rows, "deploy", _refusing("rehearsal_rollback"))
    monkeypatch.setattr(rows, "boot", lambda *_given: pytest.fail("the boot ran"))
    status, out, _, report = _rehearsal(dump, tmp_path, capsys)
    assert (status, report) == (1, "")
    assert f"{_AGAIN} refused: exit 1:\nplanted\n" in out
    assert "\nrollback: " not in out


@pytest.mark.cluster
def test_base_s_step_moving_a_row_of_cut_s_copy_fails_the_rollback(dump, tmp_path, capsys,
                                                                   monkeypatch):
    """A cent base's step, run again on cut's deployed copy, moves: the rollback fails, the
    row summed, exit 1, though base and cut store the same rows; base still boots."""
    planted: list[tuple[str, str, str]] = []
    monkeypatch.setattr(rows, "deploy", _planting(
        "rehearsal_rollback", lambda url: planted.append(rows.plant_cent(url))))
    status, out, _, report = _rehearsal(dump, tmp_path, capsys)
    table, _, column = planted[0]
    assert (status, report) == (1, "")
    assert (f"{_AGAIN} moved 1 rows:\n{table}: 1 rows differ (0 only on cut, 0 only on rolled "
            f"back, 1 changed)\n  {column}: 1 rows, total change +0.01\n") in out
    assert "cannot boot" not in out and "\nrollback: " not in out


@pytest.mark.cluster
def test_base_that_cannot_boot_on_cut_s_copy_fails_the_rollback(dump, tmp_path, capsys,
                                                                 monkeypatch):
    """Cut's deployed copy without the table base's boot queries (empty, so no row differs):
    base's step runs again and moves nothing, but base cannot boot, quoted, exit 1."""
    monkeypatch.setattr(rows, "deploy", _planting("rehearsal_rollback", _executing(
        "DROP TABLE salary.pay_stubs CASCADE")))
    status, out, _, report = _rehearsal(dump, tmp_path, capsys)
    assert (status, report) == (1, "")
    assert "ROLLBACK FAILS: base cannot boot on cut's deployed copy: exit 1:\n" in out
    assert 'relation "salary.pay_stubs" does not exist' in out
    assert _AGAIN not in out and "\nrollback: " not in out
