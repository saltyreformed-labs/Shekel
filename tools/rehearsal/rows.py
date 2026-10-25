"""The rows (step X-cx's K1b-1): every stored row of the dump, once base's deploy step and
once cut's has run on it, compared row by row, the noise gate and the planted controls run
every time (``design.md`` s.3 items 2, 3, 5, 8 and 10).

THE DEPLOY IS PRODUCTION'S.  Each state's clone runs that state's ``scripts/init_database.py``
from its worktree and under its venv, as entrypoint step 3 runs it, in an environment BUILT
WHOLE (:func:`side_environment`): nothing of the shell that runs the kit reaches it, so an
operator's ``DATABASE_URL_APP``, ``RATELIMIT_STORAGE_URI`` or ``SHEKEL_FAKE_TODAY`` cannot.
What production's image and compose file give the app is read from the side's own tree, so a
cut that changes it is measured with it.  The step runs through the side probe's
``side/deploy.py``, under ONE instant every side's clock is pinned at: noon of the dump's civil
day in base's zone, computed once a run (:func:`pinned_instant`), so a run that crosses
midnight deploys every side on one day.

EVERY TABLE IN THE CATALOG, every row, every column.  A table with a primary key is compared
row by row by that key; one without, or one whose key is not the same on both sides, as a
multiset of its rows, and the report names it.  ``system.audit_log`` is a multiset of what each
event wrote and when: never its id or database user, since its ids come from a sequence every
deploy advances.  Each row is hashed in SQL; only a row that differs is fetched whole.

THE STAMPS ARE COMPARED, BUT FOR THE ONES A DEPLOY WROTE (``balance:R-BAL331``).  A stamp is a
timestamp whose default is a read of the server's clock and nothing else, as both sides'
catalogs say (:func:`stamps`); PostgreSQL's clock writes it, which no pin reaches, so two
deploys run at two moments write two values.  The cluster's clock is read once just before the
first deploy: THE LINE (:func:`read_line`).  A stamp later than the line reads as ``deploy``
(:class:`Reading`); every other stamp is compared exactly, the audit log's own clock and the
stamps in the rows its events wrote included.  The line is checked every run: a stamp of the
restored dump later than it stops the run, named.

THE NOISE GATE: base is deployed on two clones, and both must read identical.  A difference
there is the kit's failure, never the release's (exit 2).  It catches a column the server's
clock writes that the catalogs do not call a stamp (one an UPDATE alone sets to the clock, with
no clock default: :func:`stamps`) only where base's deploy writes one; on a dump at base's own
revision that deploy writes nothing, and such a column only cut's deploy writes is a difference
(exit 1), never an exit 0.

THE PLANTED CONTROLS, every run, each on a clone of base's deployed copy, nothing else
touched (triggers and foreign-key checks are off for the plant): a cent added to a numeric
column, a byte changed in a text column, a row removed, and a stamp moved to a moment earlier
than both itself and the line.  The comparison must find each exactly.  Then the fifth: the
clock written into the same stamp on two clones in turn, which the comparison of the two must
NOT find, and which the noise gate cannot grade (on a dump at base's own revision its deploy
writes nothing).  Any control that fails means the run measured nothing (exit 2).

THE ROLLBACK runs exactly when production's would once cut's deploy has stored its copy: when
base's tree carries a migration script for every revision that copy is stamped at
(:func:`base_resolves`, which mirrors ``deploy/shekel-deploy.sh``'s ``image_resolves_revisions``).
Then, in entrypoint order, base's deploy step runs again on a clone of cut's deployed copy and
must exit 0 and move no row, and base's app must boot there (``side/boot.py``).  When base's
tree does not know the revision, that script would refuse the rollback and name the dump to
restore, and the run says so: the copy's migration version is already a row that differs.  When
cut's deploy refused, its one transaction left the dump as it was, so production's rollback is
base's own deploy on it, which the run has already measured (the boot aside), and none runs.

What it does NOT compare is printed on every run (:data:`NOT_COMPARED`), the audit log's
dropped columns and the stamps' one hole among it, and with it the limit the days put on a run
(:func:`day_limit`), so an exit 0 is never read as a claim about them.

Usage, which brings a bench up (:mod:`.bench`), deploys, compares and tears it down::

    python -m tools.rehearsal.rows --dump <pg_dump -Fc file> --base <revision> \\
        --cut <revision> --report <file>

Exit status: 0 no row differs and the rollback holds; 1 rows differ (each one in the report,
summed per table and column on stdout), cut's deploy refused the copy base's deployed, or the
rollback to base on cut's deployed copy fails (named); 2 nothing was measured:
the bench, the dump's stamps against the line (:func:`read_line`), base's deploy, the noise
gate, a control, or the run itself (a file it could not write, a database error) failed, each
named.  A failure of the run is never exit 1, which says rows differ.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg import sql

from tools.rehearsal import bench

#: A table's ``(schema, name)``.
Table = tuple[str, str]
#: A row as compared: a table's JSON object, or the audit log's array (:meth:`Reading.image`).
Image = dict[str, object] | list[object]
AUDIT_LOG: Table = ("system", "audit_log")
#: What a stamp later than the line reads as (:class:`Reading`).
DEPLOY = "deploy"
#: The reads of the server's clock a timestamp's default can be, as ``pg_get_expr`` prints
#: them.
_CLOCK = (r"now\(\)|clock_timestamp\(\)|statement_timestamp\(\)|transaction_timestamp\(\)"
          r"|(CURRENT_TIMESTAMP|LOCALTIMESTAMP)(\(\d+\))?")
#: A stamp's default: a clock read and nothing else, so ``now() + interval`` is not one.
_STAMP_DEFAULT = f"^({_CLOCK})$"
#: A day's default: the server's day, or a clock read a date column keeps the day of.
_DAY_DEFAULT = f"^(CURRENT_DATE|{_CLOCK})$"


def _filled(types: str, default: str) -> sql.Composable:
    """The columns of the catalog's table ``c`` of one of ``types`` (SQL ``regtype`` values)
    whose default as ``pg_get_expr`` prints it matches ``default``, in column order."""
    return sql.SQL(
        "ARRAY(SELECT a.attname::text FROM pg_attribute a "
        "JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
        "WHERE a.attrelid = c.oid AND NOT a.attisdropped AND a.atttypid IN ({types}) "
        "AND pg_get_expr(d.adbin, d.adrelid) ~ {default} ORDER BY a.attnum)").format(
            types=sql.SQL(types), default=sql.Literal(default))


#: Every table: the columns of its primary key in order (none for a table without one), its
#: stamps (a timestamp whose default is :data:`_STAMP_DEFAULT`) and its days (a date whose
#: default is :data:`_DAY_DEFAULT`).
_CATALOG = sql.SQL("""
SELECT n.nspname, c.relname,
       ARRAY(SELECT a.attname::text
             FROM pg_index i
             CROSS JOIN LATERAL unnest(i.indkey::int2[]) WITH ORDINALITY AS k(attnum, position)
             JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.attnum
             WHERE i.indrelid = c.oid AND i.indisprimary
             ORDER BY k.position),
       {stamps}, {days}
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition
  AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg\\_%'
ORDER BY 1, 2
""").format(stamps=_filled("'timestamptz'::regtype, 'timestamp'::regtype", _STAMP_DEFAULT),
            days=_filled("'date'::regtype", _DAY_DEFAULT))
#: Columns a control may be planted in, in catalog order, none that a generation computes.
_PLANTABLE = """
SELECT table_schema, table_name, column_name FROM information_schema.columns
WHERE data_type = ANY(%s) AND is_generated = 'NEVER'
  AND (numeric_scale IS NULL OR numeric_scale >= 2)
ORDER BY table_schema, table_name, ordinal_position
"""
_SIDE = Path(__file__).resolve().parent / "side"
#: The side probe that runs a side's deploy step under the pinned instant, run by path.
_DEPLOY_PROBE = _SIDE / "deploy.py"
#: The side probe that boots a side's app under the pinned instant, run by path.
_BOOT_PROBE = _SIDE / "boot.py"
#: Where a side's tree holds its migration scripts, as its image does: ``shekel-deploy.sh``'s
#: ``IMAGE_MIGRATIONS_DIR`` default, relative to the image's working directory, which holds
#: the tree (the ``Dockerfile``'s ``COPY . .``).
_MIGRATIONS = Path("migrations") / "versions"
#: A rate-limit store ProdConfig accepts (it refuses ``memory:``) that names no server that
#: could answer (``.invalid`` never resolves, RFC 6761): the deploy serves no request, and a
#: deploy that reached for the store would fail by name rather than pass on a live one.
_UNREACHABLE_LIMITER = "redis://rehearsal.invalid:6379/0"
#: The side's ``Dockerfile`` lines that set its image's environment.
_IMAGE_ENV = re.compile(r"^ENV ([A-Z_][A-Z0-9_]*)=(\S+)$", re.MULTILINE)
#: The production compose file's pin of the app's civil timezone.
_COMPOSE_TZ = re.compile(r"^\s+TZ: (\S+)$", re.MULTILINE)
_COMPOSE = Path("deploy") / "docker-compose.prod.yml"
#: ``PYTHONHASHSEED``, fixed on every side (design s.3 item 5).
_HASH_SEED = "0"
#: Switches python-dotenv off, so ``app/config.py``'s ``load_dotenv()`` reads no ``.env``, as
#: in production, whose image holds none (``.dockerignore``).  Without it a ``.env`` in any
#: directory above the side's worktree (the walk ``find_dotenv()`` makes up to ``/``) would
#: set what this environment leaves unset: ``DATABASE_URL_APP`` first, which the app prefers
#: to ``DATABASE_URL`` and only the deploy step blanks, so the boot would connect where it
#: named.  Honoured by every python-dotenv pinned since 2026-03-21 (1.2.2 on).
_NO_DOTENV = ("PYTHON_DOTENV_DISABLED", "1")
#: The cent the money control adds.
CENT = Decimal("0.01")
#: How much of a refused deploy's stderr the report quotes.
_QUOTED_LINES = 20
#: Printed on every run: what the comparison does not see (the coordinator's D3 ruling).
NOT_COMPARED = (
    "not compared: the audit log's id and db_user, the schema (tables, columns, "
    "constraints, indexes), privileges (the dump is "
    "restored without them and no shekel_app role exists, so the deploy's grants do nothing "
    "and the rollback's boot connects as the owner, where production's app connects as "
    "shekel_app), "
    "sequences, entrypoint step 2 (the role), and the rows after a first page load (K1c); "
    f"and a stamp later than the line reads as {DEPLOY!r} whatever wrote it, so two such "
    "values a deploy wrote by anything but its own clock (now() + interval) read alike")


class _NoPlant(bench.BenchError):
    """No row of the copy could take a planted control."""


@dataclass(frozen=True)
class Moved:
    """One row that differs: its table, its key (the primary key as JSON; empty for a
    multiset table's row), and its image on each side, None where it is absent."""

    table: str
    key: str
    before: Image | None
    after: Image | None


@dataclass(frozen=True)
class Held:
    """What one side's catalog says of a table (:data:`_CATALOG`): the columns of its primary
    key in order (none without one), its stamps and its days."""

    key: tuple[str, ...]
    stamps: tuple[str, ...] = ()
    days: tuple[str, ...] = ()


@dataclass(frozen=True)
class Reading:
    """How a comparison reads the rows: each table's stamps (:func:`stamps`) and the line
    (:func:`read_line`).  A stamp later than the line reads as :data:`DEPLOY`; every other
    stamp, and every other column, is read exactly."""

    stamps: dict[Table, tuple[str, ...]]
    line: datetime

    def later(self, document: sql.Composable) -> sql.Composable:
        """Whether the stamp named ``k`` of the JSON object ``document`` is later than the
        line: THE one spelling of the rule, which the image and the dump's check both read."""
        return sql.SQL("({} ->> k)::timestamptz > {}").format(document, sql.Literal(self.line))

    def parts(self, table: Table) -> list[tuple[str, sql.Composable, sql.Composable]]:
        """What of a row of ``table`` (aliased ``x``) holds stamps, each as the prefix its
        stamps are named by, a JSON object, and its stamps' names (a ``text[]``): the row
        itself, when it has stamps; for the audit log, its own clock and the two images of
        the row each event wrote, whose stamps are those of the table the event names."""
        own = sql.SQL("{}::text[]").format(sql.Literal(list(self.stamps.get(table, ()))))
        if table != AUDIT_LOG:
            return [("", sql.SQL("to_jsonb(x)"), own)] if self.stamps.get(table) else []
        audited = sql.SQL(
            "ARRAY(SELECT jsonb_array_elements_text({}::jsonb -> "
            "(x.table_schema || '.' || x.table_name)))").format(sql.Literal(json.dumps(
                {_name(stamped): list(names) for stamped, names in self.stamps.items()
                 if names})))
        return [("", sql.SQL("jsonb_build_object('executed_at', x.executed_at)"), own),
                ("old_data.", sql.SQL("x.old_data"), audited),
                ("new_data.", sql.SQL("x.new_data"), audited)]

    def image(self, table: Table) -> sql.Composable:
        """A row of ``table`` (aliased ``x``) as compared, each stamp later than the line read
        as :data:`DEPLOY`: the audit log's event (what it wrote and when, never its id or
        database user, as an array), or the row whole."""
        stamped = [sql.SQL(
            "({document} || coalesce((SELECT jsonb_object_agg(k, {deploy}::text) "
            "FROM unnest({names}) AS k WHERE {later}), '{{}}'::jsonb))").format(
                document=document, deploy=sql.Literal(DEPLOY), names=names,
                later=self.later(document))
            for _, document, names in self.parts(table)]
        if table == AUDIT_LOG:
            return sql.SQL(
                "jsonb_build_array(x.table_schema, x.table_name, x.operation, x.row_id, "
                "x.changed_fields, {}, {}, x.user_id, {})").format(
                    stamped[1], stamped[2], stamped[0])
        return stamped[0] if stamped else sql.SQL("to_jsonb(x)")


def _name(table: Table) -> str:
    """``schema.name``."""
    return ".".join(table)


def _identifier(table: Table) -> sql.Identifier:
    """The table as SQL."""
    return sql.Identifier(*table)


def _key(columns: tuple[str, ...]) -> sql.Composable:
    """A row's primary key as the text of a JSON array, of ``columns`` of the row ``x``."""
    return sql.SQL("jsonb_build_array({})::text").format(
        sql.SQL(", ").join(sql.Identifier("x", column) for column in columns))


def catalog(connection: psycopg.Connection) -> dict[Table, Held]:
    """Every table of the database and what its catalog says of it (:class:`Held`)."""
    return {(schema, name): Held(tuple(key), tuple(stamped), tuple(dated))
            for schema, name, key, stamped, dated
            in connection.execute(_CATALOG).fetchall()}


def keys(before: dict[Table, Held], after: dict[Table, Held]) -> dict[Table, tuple[str, ...]]:
    """The key each table of either side is compared by: its primary key when both sides that
    hold it agree on one, else none (a multiset), the audit log always a multiset."""
    compared: dict[Table, tuple[str, ...]] = {}
    for table in sorted(before.keys() | after.keys()):
        held = {side[table].key for side in (before, after) if table in side}
        compared[table] = (held.pop() if len(held) == 1 and table != AUDIT_LOG else ())
    return compared


def stamps(before: dict[Table, Held], after: dict[Table, Held]
           ) -> dict[Table, tuple[str, ...]]:
    """The stamps each table of either side is read through: the columns every side that
    holds the table calls a stamp.  Where the two catalogs disagree on a column (a cut that
    adds or drops a clock default), it is compared exactly, since reading it as a stamp on
    both sides could hide a value one side wrote by other means, and comparing it exactly
    can only show one."""
    compared: dict[Table, tuple[str, ...]] = {}
    for table in sorted(before.keys() | after.keys()):
        held = [side[table].stamps for side in (before, after) if table in side]
        compared[table] = tuple(column for column in held[0]
                                if all(column in other for other in held[1:]))
    return compared


def days(before: dict[Table, Held], after: dict[Table, Held]) -> list[str]:
    """Every day either side holds, as ``schema.table.column``: a date the server's clock
    fills, which no pin reaches and no line can tell, so it is compared exactly
    (:func:`day_limit`)."""
    return sorted({f"{_name(table)}.{column}" for side in (before, after)
                   for table, held in side.items() for column in held.days})


def read(connection: psycopg.Connection, present: dict[Table, Held],
         compared: dict[Table, tuple[str, ...]], reading: Reading
         ) -> dict[Table, Counter[tuple[str, str]]]:
    """Each ``present`` table's rows as ``(key, hash)`` pairs, counted: the key is empty where
    the table is compared as a multiset (``compared`` says), and the hash is the MD5 of the
    row's image (:meth:`Reading.image`)."""
    rows: dict[Table, Counter[tuple[str, str]]] = {}
    for table in present:
        columns = compared[table]
        if columns:
            query = sql.SQL("SELECT {}, md5(({})::text), 1 FROM {} x").format(
                _key(columns), reading.image(table), _identifier(table))
        else:
            query = sql.SQL("SELECT '', md5(({})::text), count(*) FROM {} x GROUP BY 2").format(
                reading.image(table), _identifier(table))
        rows[table] = Counter({(key, digest): count
                               for key, digest, count in connection.execute(query).fetchall()})
    return rows


def _images(connection: psycopg.Connection, table: Table, columns: tuple[str, ...],
            wanted: list[str], reading: Reading) -> dict[str, Image]:
    """The images of ``table``'s rows, by key (``columns``) or, for a multiset, by hash, of
    those ``wanted``, read as :func:`read` hashed them."""
    if not wanted:
        return {}
    image = reading.image(table)
    by = _key(columns) if columns else sql.SQL("md5(({})::text)").format(image)
    query = sql.SQL("SELECT DISTINCT ON (1) {by}, ({image})::text FROM {table} x "
                    "WHERE {by} = ANY(%s)").format(by=by, image=image,
                                                   table=_identifier(table))
    return {found: json.loads(text, parse_float=Decimal)
            for found, text in connection.execute(query, [wanted]).fetchall()}


def compare(before_url: str, after_url: str, line: datetime) -> list[Moved]:
    """Every row that differs between two databases, table by table in catalog order, a
    stamp later than ``line`` read as :data:`DEPLOY` (:class:`Reading`)."""
    moved: list[Moved] = []
    with psycopg.connect(before_url) as before, psycopg.connect(after_url) as after:
        held = catalog(before), catalog(after)
        compared = keys(*held)
        reading = Reading(stamps(*held), line)
        rows = (read(before, held[0], compared, reading),
                read(after, held[1], compared, reading))
        for table, columns in compared.items():
            counted = rows[0].get(table, Counter()), rows[1].get(table, Counter())
            if counted[0] != counted[1]:
                moved.extend(_parted((before, after), table, columns, counted, reading))
    return moved


def _parted(sides: tuple[psycopg.Connection, psycopg.Connection], table: Table,
            columns: tuple[str, ...],
            counted: tuple[Counter[tuple[str, str]], Counter[tuple[str, str]]],
            reading: Reading) -> list[Moved]:
    """The rows of ``table`` that the two sides' reads (``counted``, before then after) do
    not share, fetched whole from their side: a keyed row on both sides once, with both
    images; a multiset's row once for each time its count differs."""
    gone, came = counted[0] - counted[1], counted[1] - counted[0]
    index = 0 if columns else 1
    was = _images(sides[0], table, columns, sorted({pair[index] for pair in gone}), reading)
    now = _images(sides[1], table, columns, sorted({pair[index] for pair in came}), reading)
    if columns:
        return [Moved(_name(table), key, was.get(key), now.get(key))
                for key in sorted({key for key, _ in gone + came})]
    return ([Moved(_name(table), "", was[digest], None)
             for (_, digest), times in sorted(gone.items()) for _ in range(times)]
            + [Moved(_name(table), "", None, now[digest])
               for (_, digest), times in sorted(came.items()) for _ in range(times)])


def _held(before_url: str, after_url: str) -> tuple[dict[Table, Held], dict[Table, Held]]:
    """Each database's catalog (:func:`catalog`)."""
    with psycopg.connect(before_url) as before, psycopg.connect(after_url) as after:
        return catalog(before), catalog(after)


def multisets(before_url: str, after_url: str) -> list[str]:
    """The tables either database holds that are compared as multisets, named for the report."""
    return [_name(table) for table, columns in keys(*_held(before_url, after_url)).items()
            if not columns]


def day_limit(before_url: str, after_url: str) -> str:
    """The limit the days either database holds (:func:`days`) put on a run, printed with
    :data:`NOT_COMPARED`."""
    named = ", ".join(days(*_held(before_url, after_url))) or "none"
    return (f"compared, though the server's clock fills them: the days {named}, each the "
            "server's real day where a deploy inserts a row without one, so deploys either "
            "side of midnight in the server's zone differ by it (a false difference, never a "
            "false pass)")


def after_line(connection: psycopg.Connection, reading: Reading) -> list[str]:
    """Each stamp of the database on ``connection`` holding a value later than the line, as
    ``schema.table.column`` (``system.audit_log.new_data.created_at`` for a stamp of the rows
    its events wrote), by the rule the image reads (:meth:`Reading.later`)."""
    late: list[str] = []
    for table in reading.stamps:
        for prefix, document, names in reading.parts(table):
            query = sql.SQL("SELECT DISTINCT k FROM {table} x CROSS JOIN LATERAL unnest({names}) "
                            "AS k WHERE {later} ORDER BY 1").format(
                                table=_identifier(table), names=names,
                                later=reading.later(document))
            late.extend(f"{_name(table)}.{prefix}{stamp}"
                        for (stamp,) in connection.execute(query).fetchall())
    return late


def read_line(url: str) -> datetime:
    """The line: the cluster's clock, read once on the copy at ``url``, base's before its
    deploy and so the restored dump, just before the first deploy; then no stamp of that copy
    may be later than it.

    Raises:
        bench.BenchError: when one is (a clock skewed between production and the bench, or
            data dated ahead), each named, since a stamp a deploy wrote could not be told
            from it.
        psycopg.Error: when the copy cannot be read.
    """
    with psycopg.connect(url) as connection:
        (line,) = connection.execute("SELECT clock_timestamp()").fetchone()
        held = catalog(connection)
        late = after_line(connection, Reading(stamps(held, held), line))
    if late:
        raise bench.BenchError(
            f"the dump holds stamps later than the cluster's clock ({line.isoformat()}) just "
            "before the first deploy, so a stamp a deploy wrote cannot be told from them: "
            + ", ".join(late))
    return line


def _sides(row: Moved) -> tuple[dict[str, object], dict[str, object]] | None:
    """A row's two images when it is on both sides as a table's row (never an audit event,
    which is an array), else None."""
    if isinstance(row.before, dict) and isinstance(row.after, dict):
        return row.before, row.after
    return None


def changed_columns(row: Moved) -> list[str]:
    """The columns whose value differs in a row on both sides (none for an audit event or a
    row on one side only); a column one side lacks differs."""
    sides = _sides(row)
    if sides is None:
        return []
    was, now = sides
    return sorted(column for column in was.keys() | now.keys()
                  if column not in was or column not in now or was[column] != now[column])


def _number(value: object) -> Decimal | None:
    """``value`` as a Decimal when it is a JSON number (never a boolean), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return None
    return Decimal(value)


def _change(row: Moved, column: str) -> Decimal | None:
    """How far ``column`` moved in a row on both sides, when it is a number on both."""
    sides = _sides(row)
    if sides is None:
        return None
    was, now = (_number(image.get(column)) for image in sides)
    return None if was is None or now is None else now - was


def summary(moved: list[Moved], before: str, after: str) -> list[str]:
    """The differing rows summed per table and per column: how many rows, and the total
    change of a column whose every changed value is a number on both sides."""
    lines: list[str] = []
    for table in dict.fromkeys(row.table for row in moved):
        rows = [row for row in moved if row.table == table]
        only_before = sum(1 for row in rows if row.after is None)
        only_after = sum(1 for row in rows if row.before is None)
        lines.append(f"{table}: {len(rows)} rows differ ({only_before} only on {before}, "
                     f"{only_after} only on {after}, {len(rows) - only_before - only_after} "
                     "changed)")
        changes: dict[str, list[Decimal | None]] = {}
        for row in rows:
            for column in changed_columns(row):
                changes.setdefault(column, []).append(_change(row, column))
        for column, deltas in changes.items():
            numbers = [delta for delta in deltas if delta is not None]
            total = (f", total change {sum(numbers, Decimal(0)):+}"
                     if len(numbers) == len(deltas) else "")
            lines.append(f"  {column}: {len(deltas)} rows{total}")
    return lines


def _shown(value: object) -> str:
    """A value as the report writes it: JSON, its Decimals as written."""
    return json.dumps(value, default=str, sort_keys=True)


def report(moved: list[Moved], before: str, after: str) -> Iterator[str]:
    """One line per differing row: its table and key, and the row where it is on one side
    only or is an audit event, else each changed column's value before and after."""
    for row in moved:
        where = f"{row.table} {row.key or '(no key)'}"
        sides = _sides(row)
        if row.after is None:
            yield f"{where}: only on {before}: {_shown(row.before)}"
        elif row.before is None or sides is None:
            yield f"{where}: only on {after}: {_shown(row.after)}"
        else:
            yield f"{where}: " + "; ".join(
                f"{column} {_shown(sides[0].get(column))} -> {_shown(sides[1].get(column))}"
                for column in changed_columns(row))


def image_environment(worktree: Path) -> dict[str, str]:
    """What production gives the app beside its secrets and URLs, read from the side's own
    tree: every ``ENV`` of its ``Dockerfile`` but ``PATH`` (which names the image's venv),
    and the app's ``TZ`` from its production compose file.

    Raises:
        bench.BenchError: when either file cannot be read, or the compose file pins no
            single ``TZ``.
    """
    try:
        dockerfile = (worktree / "Dockerfile").read_text(encoding="utf-8")
        compose = (worktree / _COMPOSE).read_text(encoding="utf-8")
    except OSError as exc:
        raise bench.BenchError(f"{worktree} cannot give production's environment: {exc}") \
            from exc
    zones = _COMPOSE_TZ.findall(compose)
    if len(zones) != 1:
        raise bench.BenchError(f"{worktree / _COMPOSE} pins {len(zones)} TZ lines, not one")
    environment = {name: value for name, value in _IMAGE_ENV.findall(dockerfile)
                   if name != "PATH"}
    environment["TZ"] = zones[0]
    return environment


def side_environment(state: bench.State, url: str, secret_key: str) -> dict[str, str]:
    """The whole environment a side probe runs in, its deploy and its boot, inheriting
    nothing: production's (:func:`image_environment`), its configuration, the copy's URL, a
    secret key of the run's, a rate-limit store no deploy could reach, the fixed hash seed,
    python-dotenv switched off (:data:`_NO_DOTENV`), and the side's venv first on ``PATH``.

    Raises:
        bench.BenchError: when the side's tree cannot give production's environment.
    """
    return {
        **image_environment(state.worktree),
        "FLASK_ENV": "production",
        "SECRET_KEY": secret_key,
        "DATABASE_URL": url,
        "RATELIMIT_STORAGE_URI": _UNREACHABLE_LIMITER,
        "PYTHONHASHSEED": _HASH_SEED,
        _NO_DOTENV[0]: _NO_DOTENV[1],
        "PATH": os.pathsep.join((str(state.python.parent), "/usr/bin", "/bin")),
    }


def pinned_instant(made: bench.Bench) -> datetime:
    """The one instant every side's clock is pinned at in a run: noon of the dump's civil day
    (:meth:`bench.Listing.noon`) in the zone base's production compose file pins
    (:func:`image_environment`).

    Raises:
        bench.BenchError: when base's tree cannot give production's environment.
    """
    base = made.made["base"]
    return made.listing.noon(image_environment(base.worktree)["TZ"])


def _probe(probe: Path, state: bench.State, url: str, secret_key: str, moment: datetime
           ) -> str | None:
    """Run the side probe ``probe`` for ``state`` on the copy at ``url``, its clock pinned at
    ``moment``, from the side's worktree under its venv in :func:`side_environment`; None when
    it exits 0, else what it said last.

    Raises:
        bench.BenchError: when the side's interpreter cannot be run, or its tree cannot give
            production's environment.
    """
    done = bench.run([str(state.python), str(probe), moment.isoformat()],
                     side_environment(state, url, secret_key), cwd=state.worktree)
    if not done.returncode:
        return None
    said = (done.stderr or done.stdout).strip().splitlines()[-_QUOTED_LINES:]
    return f"exit {done.returncode}:\n" + "\n".join(said)


def deploy(state: bench.State, url: str, secret_key: str, moment: datetime) -> str | None:
    """Run ``state``'s deploy step on the copy at ``url`` with its clock pinned at ``moment``
    (``side/deploy.py``, by :func:`_probe`); None when it exits 0, else what it said last.

    Raises:
        bench.BenchError: as :func:`_probe`.
    """
    return _probe(_DEPLOY_PROBE, state, url, secret_key, moment)


def boot(state: bench.State, url: str, secret_key: str, moment: datetime) -> str | None:
    """Boot ``state``'s app on the copy at ``url`` with its clock pinned at ``moment`` and
    answer one query (``side/boot.py``, by :func:`_probe`); None when it exits 0, else what it
    said last.

    Raises:
        bench.BenchError: as :func:`_probe`.
    """
    return _probe(_BOOT_PROBE, state, url, secret_key, moment)


def stamped_revisions(url: str) -> list[str]:
    """The migration versions the copy at ``url`` is stamped at (``public.alembic_version``),
    sorted."""
    with psycopg.connect(url) as connection:
        return sorted(revision for (revision,) in connection.execute(
            "SELECT version_num FROM public.alembic_version").fetchall())


def base_resolves(worktree: Path, revisions: list[str]) -> bool:
    """Whether the tree carries a migration script for every one of ``revisions``: a file of
    its :data:`_MIGRATIONS` named ``<revision>_*.py``.

    It MIRRORS ``deploy/shekel-deploy.sh``'s ``image_resolves_revisions`` over
    ``image_migration_revisions``' listing (the basenames of the image's
    ``migrations/versions/*.py``), the predicate that script's rollback is taken by (its
    ``repin_is_safe``): Alembic resolves a revision by finding the script whose id it is.  As
    there, a revision matches a name only up to its ``_``, and an empty list resolves; that
    script's ``repin_is_safe`` refuses an empty ``alembic_version`` before it asks, which the
    copy of a deploy that exited 0 never is (the step stamps it).
    """
    listing = [path.name for path in (worktree / _MIGRATIONS).glob("*.py")]
    return all(any(name.startswith(f"{revision}_") for name in listing)
               for revision in revisions)


def rollback(made: bench.Bench, secret_key: str, moment: datetime, line: datetime) -> bool:
    """Roll back to base on cut's deployed copy exactly when production's rollback would
    (:func:`base_resolves`), printing the verdict; False when the rollback fails.

    In entrypoint order: base's deploy step runs again on a clone of cut's deployed copy and
    must exit 0 (a refusal is production's container dying at step 3, so nothing boots) and
    move no row, compared with cut's deployed copy through ``line`` (:func:`compare`); then
    base's app must boot on the clone.

    Raises:
        bench.BenchError: when a side probe cannot be run.
        psycopg.Error: when a copy cannot be read or cloned.
    """
    base, cut = made.made["base"], made.made["cut"]
    revisions = stamped_revisions(cut.url)
    stamped = ", ".join(revisions)
    if not base_resolves(base.worktree, revisions):
        print(f"rollback: base's image does not know the migration version cut's copy is "
              f"stamped at ({stamped}), so shekel-deploy.sh would refuse the rollback and name "
              "the dump to restore: rolling back past this release is a restore")
        return True
    back = bench.State(base.name, base.commit, base.worktree, "rehearsal_rollback",
                       made.clone(cut.database, "rehearsal_rollback"), base.python)
    again = "base's deploy step, run again on cut's deployed copy,"
    refused = deploy(back, back.url, secret_key, moment)
    if refused is not None:
        print(f"ROLLBACK FAILS: {again} refused: {refused}")
        return False
    failures: list[str] = []
    moved = compare(cut.url, back.url, line)
    if moved:
        failures.append(f"ROLLBACK FAILS: {again} moved {len(moved)} rows:\n"
                        + "\n".join(summary(moved, "cut", "rolled back")))
    unbooted = boot(back, back.url, secret_key, moment)
    if unbooted is not None:
        failures.append(f"ROLLBACK FAILS: base cannot boot on cut's deployed copy: {unbooted}")
    for failure in failures:
        print(failure)
    if not failures:
        print(f"rollback: base's image knows the migration version cut's copy is stamped at "
              f"({stamped}); base boots on cut's deployed copy, and base's deploy step, run "
              "again on it, exits 0 and moves no row")
    return not failures


#: Builds a control's statement from its table, its column (None for a removal) and the
#: match of the row's key; the statement takes the key as ``%s``.
_Change = Callable[[sql.Identifier, sql.Identifier | None, sql.Composable], sql.Composable]


@dataclass(frozen=True)
class _Where:
    """Where a control may be planted: ``places`` gives each ``(table, column)`` of a copy in
    catalog order, from a connection to it and its catalog (the column empty for a removal),
    and ``what`` names them when no row can take it."""

    places: Callable[[psycopg.Connection, dict[Table, Held]], list[tuple[Table, str]]]
    what: str


def _typed(types: list[str]) -> _Where:
    """Every column of one of ``types`` (:data:`_PLANTABLE`)."""
    return _Where(lambda connection, _held: [
        ((schema, name), column)
        for schema, name, column in connection.execute(_PLANTABLE, [types]).fetchall()],
        f"a column of {types}")


#: Every table, for a removal.
_ANY_ROW = _Where(lambda _connection, held: [(table, "") for table in held], "a column of any")
#: Every stamp (:class:`Held`).
_A_STAMP = _Where(lambda _connection, held: [
    (table, stamp) for table, shape in held.items() for stamp in shape.stamps], "a stamp")


def _plant(url: str, where: _Where, change: _Change) -> tuple[str, str, str]:
    """Plant a control in the first row that takes it, of the first keyed table in catalog
    order with a column ``where`` names.  Triggers and foreign-key checks are off for the
    session, so the planted row is the only one that moves; a row a constraint refuses is
    passed over for the next.  Its table, key and column (empty for a removal).

    Raises:
        _NoPlant: when no row can take it.
    """
    with psycopg.connect(url) as connection:
        connection.execute("SET session_replication_role = replica")
        held = catalog(connection)
        for table, column in where.places(connection, held):
            columns = held[table].key if table in held else ()
            if not columns or table == AUDIT_LOG or column in columns:
                continue
            target = sql.Identifier(column) if column else None
            where = (sql.SQL("WHERE x.{c} IS NOT NULL AND x.{c}::text <> ''").format(c=target)
                     if target else sql.SQL(""))
            first = connection.execute(sql.SQL(
                "SELECT {key} FROM {table} x {where} ORDER BY {key} LIMIT 1").format(
                    key=_key(columns), table=_identifier(table), where=where)).fetchone()
            if first is None:
                continue
            try:
                with connection.transaction():
                    connection.execute(change(_identifier(table), target, _key(columns)),
                                       [first[0]])
            except (psycopg.errors.IntegrityError, psycopg.errors.DataError):
                continue
            return _name(table), first[0], column
    raise _NoPlant(f"no row of the copy could take a control in {where.what}")


def plant_cent(url: str) -> tuple[str, str, str]:
    """Add :data:`CENT` to the first numeric column that takes it (:func:`_plant`)."""
    return _plant(url, _typed(["numeric"]), lambda table, column, key: sql.SQL(
        "UPDATE {table} AS x SET {c} = x.{c} + {cent} WHERE {key} = %s").format(
            table=table, c=column, cent=sql.Literal(CENT), key=key))


def plant_byte(url: str) -> tuple[str, str, str]:
    """Change the last character of the first text column that takes it (:func:`_plant`),
    its length kept so a length limit cannot refuse it."""
    return _plant(url, _typed(["text", "character varying"]), lambda table, column, key: sql.SQL(
        "UPDATE {table} AS x SET {c} = left(x.{c}, -1) || CASE WHEN right(x.{c}, 1) = '#' "
        "THEN '*' ELSE '#' END WHERE {key} = %s").format(table=table, c=column, key=key))


def plant_removal(url: str) -> tuple[str, str, str]:
    """Remove the first row of the first keyed table that has one (:func:`_plant`)."""
    return _plant(url, _ANY_ROW, lambda table, _column, key: sql.SQL(
        "DELETE FROM {table} AS x WHERE {key} = %s").format(table=table, key=key))


def plant_earlier(url: str, line: datetime) -> tuple[str, str, str]:
    """Move the first stamp that takes it to a microsecond before both itself and ``line``
    (:func:`_plant`): a stamp a deploy wrote after the line is moved back across it, so the
    moved stamp is read exactly whoever wrote it."""
    return _plant(url, _A_STAMP, lambda table, column, key: sql.SQL(
        "UPDATE {table} AS x SET {c} = least(x.{c}, {line}) - interval '1 microsecond' "
        "WHERE {key} = %s").format(table=table, c=column, line=sql.Literal(line), key=key))


def plant_now(url: str) -> tuple[str, str, str]:
    """Write the server's clock into the first stamp that takes it (:func:`_plant`), as a
    deploy's own transaction would."""
    return _plant(url, _A_STAMP, lambda table, column, key: sql.SQL(
        "UPDATE {table} AS x SET {c} = now() WHERE {key} = %s").format(
            table=table, c=column, key=key))


def _stored(url: str, planted: tuple[str, str, str]) -> object:
    """The planted stamp's value as the copy at ``url`` stores it, read raw."""
    name, key, column = planted
    schema, relation = name.split(".", 1)
    with psycopg.connect(url) as connection:
        columns = catalog(connection)[(schema, relation)].key
        row = connection.execute(sql.SQL("SELECT x.{c} FROM {table} x WHERE {key} = %s").format(
            c=sql.Identifier(column), table=_identifier((schema, relation)),
            key=_key(columns)), [key]).fetchone()
    return None if row is None else row[0]


def found(moved: list[Moved], planted: tuple[str, str, str], removed: bool = False) -> bool:
    """Whether a comparison found exactly one planted control: the one row, in its table at
    its key, gone (``removed``) or changed in its column and no other."""
    if len(moved) != 1:
        return False
    row, (table, key, column) = moved[0], planted
    if (row.table, row.key) != (table, key):
        return False
    if removed:
        return row.before is not None and row.after is None
    return changed_columns(row) == [column]


#: Each control found exactly: its name, its plant (given the line), whether it removes a
#: row, and what else must hold of the one row found (the cent's amount).
_CONTROLS: tuple[tuple[str, Callable[[str, datetime], tuple[str, str, str]], bool,
                       Callable[[Moved, str], bool]], ...] = (
    ("a cent", lambda url, _line: plant_cent(url), False,
     lambda row, column: _change(row, column) == CENT),
    ("a byte", lambda url, _line: plant_byte(url), False, lambda _row, _column: True),
    ("a removed row", lambda url, _line: plant_removal(url), True, lambda _row, _column: True),
    ("a stamp moved earlier", plant_earlier, False, lambda _row, _column: True),
)
#: The control that must NOT be found (:func:`rewritten_twice`).
_TWICE = "a stamp the clock wrote on two clones in turn"


def rewritten_twice(made: bench.Bench, base: bench.State, line: datetime) -> str | None:
    """The fifth control: on each of two clones of base's deployed copy in turn, the server's
    clock written into the same stamp (:func:`plant_now`).  What is wrong with it; None when
    the two values differ, each later than ``line``, and the comparison of the two clones
    finds nothing, both stamps read as :data:`DEPLOY`.

    Raises:
        psycopg.Error: when a clone cannot be made or read.
    """
    urls = [made.clone(base.database, f"rehearsal_control_twice_{index}") for index in (0, 1)]
    try:
        planted = [plant_now(url) for url in urls]
    except _NoPlant as exc:
        return f"{_TWICE}: {exc}"
    written = [_stored(url, planted[0]) for url in urls]
    if (planted[0] != planted[1] or written[0] == written[1]
            or not all(isinstance(value, datetime) and value > line for value in written)):
        return (f"{_TWICE} planted at {' and '.join(' '.join(place) for place in planted)} "
                f"wrote {written[0]} and {written[1]}, not two moments each later than the "
                f"line {line.isoformat()}, so it measures nothing")
    moved = compare(urls[0], urls[1], line)
    if moved:
        return (f"{_TWICE} planted at {' '.join(planted[0])} was found; the comparison found "
                f"{len(moved)} rows: " + "; ".join(summary(moved, "one", "the other")))
    return None


def controls(made: bench.Bench, base: bench.State, line: datetime) -> list[str]:
    """Plant each control on its own clone of base's deployed copy and compare it with that
    copy through ``line``, then the fifth (:func:`rewritten_twice`); what is wrong with any,
    each named (none when each was found exactly and the fifth was not found)."""
    wrong: list[str] = []
    for index, (label, plant, removed, holds) in enumerate(_CONTROLS):
        url = made.clone(base.database, f"rehearsal_control_{index}")
        try:
            planted = plant(url, line)
        except _NoPlant as exc:
            wrong.append(f"{label}: {exc}")
            continue
        moved = compare(base.url, url, line)
        if not (found(moved, planted, removed) and holds(moved[0], planted[2])):
            wrong.append(f"{label} planted at {planted[0]} {planted[1]} {planted[2]} was not "
                         f"found exactly; the comparison found {len(moved)} rows: "
                         + "; ".join(summary(moved, "base", "the control")))
    twice = rewritten_twice(made, base, line)
    if twice is not None:
        wrong.append(twice)
    return wrong


def gates(made: bench.Bench, again: bench.State, line: datetime) -> bool:
    """The noise gate (base's deployed copy against ``again``, base deployed a second time)
    and the planted controls, each through ``line`` and printed; False when either fails,
    which is the kit's failure, named on stderr, never the release's."""
    base = made.made["base"]
    began = time.monotonic()
    noise = compare(base.url, again.url, line)
    print(f"noise gate: {time.monotonic() - began:.2f} s, {len(noise)} rows differ")
    if noise:
        print("[rehearsal] base deployed twice did not read the same, so the comparison "
              "measures nothing:\n" + "\n".join(summary(noise, "base", "base again")),
              file=sys.stderr)
        return False
    wrong = controls(made, base, line)
    if wrong:
        print("[rehearsal] a planted control was not read as planted, so the comparison "
              "measures nothing:\n" + "\n".join(wrong), file=sys.stderr)
        return False
    print(f"controls: {', '.join(label for label, *_ in _CONTROLS)}, each found exactly; "
          f"{_TWICE}, not found")
    return True


def rehearse(made: bench.Bench, out: Path) -> int:
    """Read the line, deploy base twice and cut once on the bench, gate on noise and the
    controls, compare base with cut, writing every differing row to ``out``, and roll back
    to base on cut's deployed copy (:func:`rollback`); the exit status.

    Raises:
        bench.BenchError: when the dump holds a stamp later than the line (:func:`read_line`)
            or a side probe cannot be run.
        psycopg.Error: when a copy cannot be read or cloned.
    """
    base, cut = made.made["base"], made.made["cut"]
    secret_key = secrets.token_hex(32)
    moment = pinned_instant(made)
    print(f"clock pinned at {moment.isoformat()}")
    again = bench.State(base.name, base.commit, base.worktree, "rehearsal_base_again",
                        made.clone(bench.PRISTINE, "rehearsal_base_again"), base.python)
    line = read_line(base.url)
    print(f"line: {line.isoformat()}, the cluster's clock just before the first deploy; a "
          f"stamp later than it reads as {DEPLOY!r}")
    refused: dict[str, str | None] = {}
    for label, state in (("base", base), ("base, again", again), ("cut", cut)):
        began = time.monotonic()
        refused[label] = deploy(state, state.url, secret_key, moment)
        print(f"deploy {label}: {time.monotonic() - began:.2f} s, "
              + ("exit 0" if refused[label] is None else "REFUSED"))
    for label in ("base", "base, again"):
        if refused[label] is not None:
            print(f"[rehearsal] {label}'s deploy refused the dump, so nothing is compared "
                  f"(an app defect): {refused[label]}", file=sys.stderr)
            return 2
    if not gates(made, again, line):
        return 2
    print(NOT_COMPARED)
    print(day_limit(base.url, cut.url))
    print("compared as multisets: " + ", ".join(multisets(base.url, cut.url)))
    if refused["cut"] is not None:
        print(f"cut's deploy refused the copy base's deployed: {refused['cut']}")
        return 1
    began = time.monotonic()
    moved = compare(base.url, cut.url, line)
    out.write_text("".join(f"{entry}\n" for entry in report(moved, "base", "cut")),
                   encoding="utf-8")
    print(f"base against cut: {time.monotonic() - began:.2f} s, {len(moved)} rows differ "
          f"(each in {out})")
    for entry in summary(moved, "base", "cut"):
        print(entry)
    held = rollback(made, secret_key, moment, line)
    return 1 if moved or not held else 0


def main(argv: list[str] | None = None) -> int:
    """Bring a bench up on a dump with states ``base`` and ``cut``, rehearse, tear it down."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dump", type=Path, required=True, help="a pg_dump -Fc archive")
    parser.add_argument("--base", required=True, help="the revision production runs")
    parser.add_argument("--cut", required=True, help="the revision to release")
    parser.add_argument("--report", type=Path, required=True,
                        help="where every differing row is written (never into git)")
    args = parser.parse_args(argv)
    try:
        made = bench.Bench(args.dump, {"base": args.base, "cut": args.cut})
        with made:
            status = rehearse(made, args.report)
    except (bench.BenchError, OSError, psycopg.Error) as exc:
        print(f"[rehearsal] {exc}", file=sys.stderr)
        return 2
    for part, seconds in made.timings.items():
        print(f"{part}: {seconds:.2f} s")
    return status


if __name__ == "__main__":
    sys.exit(main())
