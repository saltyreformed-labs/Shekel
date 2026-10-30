"""
Shekel Budget App -- Test Fixtures

Provides reusable pytest fixtures for the test suite: a configured
test app, a freshly-cloned per-test database, an authenticated
client, and factory helpers for creating test data.

Strategy: each test gets a brand-new database cloned from
``shekel_test_template`` via ``CREATE DATABASE ... TEMPLATE ...``
(Phase 3b of
``docs/audits/test_improvements/test-performance-implementation-plan.md``),
replacing the prior per-test TRUNCATE+reseed cycle.  The per-test
isolation contract (empty ``system.audit_log``, no rows in
``budget.*`` / ``auth.*`` / ``salary.*``, full ref-data seed,
in-process ``ref_cache`` matching the seeded IDs) is bit-for-bit
identical between the two mechanisms -- only the underlying
delivery changes.

The clone uses ``STRATEGY WAL_LOG``.  Phase 3b's original
``STRATEGY FILE_COPY`` was chosen for the reflink path on the dev
box's btrfs PGDATA and is 20x SLOWER on any cluster that has not
switched durability off -- which CI's postgres service container had
not, until 2026-08-18 (since ``bank_import:X-gy`` CI runs the
wrapper's non-durable cluster).  ``_clone_worker_database`` below
carries the measurements
and the failures that decision produced; do not restore
``FILE_COPY`` without re-measuring against a durable cluster.
"""

# pylint: disable=wrong-import-position,wrong-import-order
# Imports below are intentionally ordered so the SECRET_KEY env var
# is set AND the per-pytest-worker database is cloned from
# ``shekel_test_template`` BEFORE any ``app`` module is imported.
# Two class-body reads at first-app-import time depend on this:
#
# * ``app.config.TestConfig.SQLALCHEMY_DATABASE_URI`` reads
#   ``TEST_DATABASE_URL`` -- ``_bootstrap_worker_database`` below
#   sets it to the per-session DSN.
# * Production / ``_reject_sentinel`` defends read ``SECRET_KEY``
#   with no fallback (audit finding F-016).
#
# Setting either env var after the first ``from app import ...``
# would leave the app pointed at a stale value.

import csv
import os
import pathlib
import statistics
import time
from contextlib import contextmanager
# ``datetime.time`` is aliased, NOT imported under its own name: the bare name
# ``time`` above is the stdlib MODULE, and the profile harness below calls
# ``time.perf_counter()``.  An unaliased ``from datetime import time`` shadows
# it two lines after it is imported, which is exactly what happened between
# 2026-07-26 (commit d3489728) and this line: every test errored with
# ``AttributeError: type object 'datetime.time' has no attribute
# 'perf_counter'`` the moment ``SHEKEL_TEST_FIXTURE_PROFILE=1`` was set, so the
# one instrument this project has for diagnosing test performance was dead for
# three weeks and nothing reported it (``tests/`` is outside the pylint scope
# that would have flagged the reimport).
from datetime import date, datetime, time as dt_time, timedelta
from decimal import Decimal
from urllib.parse import urlparse, urlunparse

import psycopg
from psycopg import sql

# IMPORTANT: SECRET_KEY must be set in the environment BEFORE the
# ``app`` package is imported, because ``app/config.py`` reads it at
# class-definition time via ``os.getenv("SECRET_KEY")``.  Production
# config has no fallback default (audit finding F-016), so without
# this setdefault Flask sessions in the test suite would fail to
# sign or verify.  ``setdefault`` so that a developer running pytest
# with their own real key in the environment is not overridden.
# The value is intentionally distinct from any placeholder rejected
# by ProdConfig and is at least 32 characters.
os.environ.setdefault(
    "SECRET_KEY",
    "test-suite-fixed-key-not-used-in-production-do-not-deploy",
)


# Name of the PostgreSQL template database the bootstrap clones from.
# Built by ``scripts/build_test_template.py`` and verified by
# ``scripts/build_test_db_image.py`` (``_TEMPLATE_DATABASE``); all THREE must
# resolve the same name or the suite clones a stale template.
#
# A LITERAL, NOT AN OVERRIDE (plan step ``balance:X-br-4``).  Both this and
# the prefix below were once read from ``TEST_TEMPLATE_DATABASE`` /
# ``TEST_DB_PREFIX`` so that two checkouts sharing ONE postmaster could name
# their template and their worker databases apart.  ``scripts/test.sh`` now
# gives every run a private cluster, which holds exactly one template and one
# run's worker databases, so there is nothing left to rename them away from
# and the overrides were deleted rather than kept as a second spelling of a
# constant.
_TEST_TEMPLATE_DATABASE = "shekel_test_template"
# Prefix of the PER-WORKER database names.  See the note above: one cluster
# per run, so one prefix.
_TEST_DATABASE_PREFIX = "shekel_test"
# Default admin DSN (peer auth) -- overridable via env so CI and
# developer laptops that need TCP + password can point at their own
# admin DB without code change.  Must NOT be the template DB itself:
# ``CREATE DATABASE`` and ``DROP DATABASE`` cannot run against the
# connection's own database.
_DEFAULT_ADMIN_URL = "postgresql:///postgres"
# Expected ``ref.account_types`` row count in a freshly-cloned per-
# session DB.  Sourced from ``app.ref_seeds.ACCT_TYPE_SEEDS``; any
# mismatch indicates the template is corrupt and needs a rebuild.
_EXPECTED_ACCOUNT_TYPE_COUNT = 19


# ---------------------------------------------------------------------------
# Fixture profile harness (Phase 0 of test-performance-implementation-plan)
# ---------------------------------------------------------------------------
# Permanent instrumentation around the per-test ``db`` fixture inner
# steps, gated behind ``SHEKEL_TEST_FIXTURE_PROFILE=1`` so the default
# test path is unaffected.  When the flag is set, each test appends
# one row to a per-worker CSV in ``tests/.fixture-profile/`` recording
# elapsed milliseconds for rollback / TRUNCATE main / seed_ref /
# commit / TRUNCATE audit_log / refresh_ref_cache / call / teardown.
# At session end the aggregator reads every worker CSV and prints a
# summary table whose shape matches
# ``docs/audits/test_improvements/test-performance-research.md``
# section 3.1.
#
# Why this lives here and not in a sibling module: the timer
# wrappers must be physically interleaved with the fixture body, and
# the aggregator must run from ``pytest_sessionfinish``, which is a
# conftest-level hook.  Splitting helpers into a sibling module would
# add an indirection without buying isolation -- the wrappers would
# still need direct access to ``_db`` and the fixture's local state.
#
# Why the flag is checked once at module load (not per-test): we want
# zero per-test cost when disabled.  A single module-level boolean
# costs one branch per ``with _profile_step(...)`` block at fixture
# entry -- well below the noise floor of the operations it wraps.

_FIXTURE_PROFILE_ENABLED = os.environ.get("SHEKEL_TEST_FIXTURE_PROFILE") == "1"
_FIXTURE_PROFILE_DIR = pathlib.Path(__file__).parent / ".fixture-profile"

# Step names, in column order.  Drives the CSV header, the per-test
# row writer, and the row order in the summary table.  The leading
# ``setup_`` prefix tags steps that contribute to "Fixture setup
# total" in the aggregator (vs. ``call`` and ``teardown`` which are
# reported but not part of the fixture-percent column).  The names
# match the labels in the published baseline so a future reader can
# diff the two tables cell-for-cell.
_FIXTURE_PROFILE_STEPS = (
    "setup_rollback",
    "setup_drop_db",
    "setup_clone_template",
    "setup_refresh_ref_cache",
    "call",
    "teardown",
)

# Pretty labels for each step, used only by the aggregator's print
# pass.  Kept beside _FIXTURE_PROFILE_STEPS so future edits stay in
# sync.  Phase 3b replaced ``setup_truncate_main`` /
# ``setup_seed_ref`` / ``setup_commit_after_seed`` with
# ``setup_drop_db`` + ``setup_clone_template``; the published
# baseline comparison in
# ``docs/audits/test_improvements/test-performance-implementation-plan.md``
# remains diff-able per-step because the surviving step keys
# (``setup_rollback``, ``setup_refresh_ref_cache``, ``call``,
# ``teardown``) and their labels are unchanged.
_FIXTURE_PROFILE_LABELS = {
    "setup_rollback": "rollback",
    "setup_drop_db": "DROP DATABASE WITH (FORCE)",
    "setup_clone_template": "CREATE DATABASE TEMPLATE STRATEGY WAL_LOG",
    "setup_refresh_ref_cache": "refresh_ref_cache",
    "call": "Test body (call)",
    "teardown": "Teardown",
}

# Per-worker CSV path.  ``PYTEST_XDIST_WORKER`` is ``"gw0"``,
# ``"gw1"``, ... under xdist and unset under single-process pytest
# (we use ``"main"`` for the latter, matching the bootstrap's
# ``worker_id`` convention).  Each worker writes to its own file so
# concurrent appends never contend on a lock.
_FIXTURE_PROFILE_WORKER_ID = os.environ.get("PYTEST_XDIST_WORKER", "main")
_FIXTURE_PROFILE_CSV = (
    _FIXTURE_PROFILE_DIR / f"{_FIXTURE_PROFILE_WORKER_ID}.csv"
)


def _is_xdist_master():
    """Return True for the pytest-xdist controller process.

    The controller spawns workers and runs collection but does NOT
    execute tests; it sets ``PYTEST_XDIST_TESTRUNUID`` but leaves
    ``PYTEST_XDIST_WORKER`` unset.  Workers (``gw0``, ``gw1``, ...)
    set both, and single-process runs set neither.  The harness uses
    this distinction to skip the per-test CSV setup on the master
    while still running the aggregator there (the master is the only
    process that sees every worker's output after they exit).
    """
    return (
        bool(os.environ.get("PYTEST_XDIST_TESTRUNUID"))
        and not os.environ.get("PYTEST_XDIST_WORKER")
    )


def _profile_session_init():
    """Wipe stale CSVs and prepare this process's profile file.

    Two-phase:

    1. The xdist master (or single-process run) wipes any leftover
       ``*.csv`` from a previous pytest invocation before workers
       spawn.  Without this, a previous run with ``-n 16`` would
       leave ``gw13``..``gw15.csv`` on disk and the next ``-n 12``
       run's aggregator would mistakenly include their stale rows.
       Worker subprocesses load this conftest AFTER the master, so
       the wipe is finished by the time they create their own CSVs.
    2. Every process whose conftest load happens before xdist sets
       ``PYTEST_XDIST_TESTRUNUID`` creates the dir and writes a
       header row to its worker CSV.  Empirically (pytest-xdist
       3.8 on Python 3.14) this includes both single-process runs
       AND the xdist master -- the master never runs tests, so its
       ``main.csv`` ends up as a header-only stub.  Workers
       (``gw0``..``gwN``) load conftest later, with ``TESTRUNUID``
       already set, but they are detected via ``PYTEST_XDIST_WORKER``
       not via TESTRUNUID, so the ``_is_xdist_master`` check below
       is defence-in-depth for a future xdist that sets
       ``TESTRUNUID`` earlier on the master.

    Truncating-on-init means two consecutive pytest runs with the
    same worker id do not accumulate -- the second run starts from
    a clean header row.

    No-op when ``SHEKEL_TEST_FIXTURE_PROFILE`` is unset.
    """
    if not _FIXTURE_PROFILE_ENABLED:
        return

    # Phase 1: master / single-process wipes stale CSVs.  In xdist
    # mode the master loads conftest before workers spawn, so this
    # runs first; workers see a clean directory.  (The master also
    # writes its own main.csv header in phase 2 below -- the master
    # never runs tests so that file ends up as a header-only stub.
    # The aggregator handles it correctly: ``DictReader`` returns
    # zero data rows, so the stub contributes nothing to the
    # summary.  Removing it would require an extra teardown step
    # for ~150 bytes of harmless residue.)
    if not os.environ.get("PYTEST_XDIST_WORKER") and _FIXTURE_PROFILE_DIR.exists():
        for stale_csv in _FIXTURE_PROFILE_DIR.glob("*.csv"):
            stale_csv.unlink()

    # The xdist master does not run tests -- it has nothing to
    # write into its own CSV, so skip phase 2 entirely.
    if _is_xdist_master():
        return

    # Phase 2: this worker's CSV gets a fresh header.  Open mode
    # ``"w"`` truncates; subsequent per-test rows are appended in
    # mode ``"a"`` from ``_profile_write_row``.
    _FIXTURE_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with _FIXTURE_PROFILE_CSV.open("w", newline="", encoding="utf-8") as csv_fp:
        writer = csv.writer(csv_fp)
        writer.writerow(["nodeid", "worker_id", *_FIXTURE_PROFILE_STEPS])


@contextmanager
def _profile_step(timings, step_name):
    """Record elapsed milliseconds of the wrapped block into ``timings``.

    Wraps a block of fixture code so the harness can measure each
    inner step without restructuring the fixture itself.

    Args:
        timings: Either a dict (profiling enabled) keyed by step name
            with float-millisecond values, or ``None`` (profiling
            disabled).  ``None`` short-circuits the timer so the
            wrapped block runs with zero added cost.
        step_name: One of ``_FIXTURE_PROFILE_STEPS``; the key under
            which to store the elapsed time.

    Even when the wrapped block raises, the timer captures the
    elapsed time before the exception propagates.  The exception
    itself is not suppressed -- the harness must never mask test or
    fixture errors.
    """
    if timings is None:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        timings[step_name] = (time.perf_counter() - start) * 1000.0


def _profile_new_timings():
    """Allocate a per-test timings dict, or ``None`` when disabled.

    Pre-populates every step key with ``0.0`` so the CSV row is
    well-formed even if a setup step raises and short-circuits the
    rest of the fixture body: the steps that ran record real timings,
    the bypassed ones keep their ``0.0`` floor, and the aggregator
    can still parse the row instead of choking on missing columns.
    """
    if not _FIXTURE_PROFILE_ENABLED:
        return None
    return {step: 0.0 for step in _FIXTURE_PROFILE_STEPS}


def _profile_write_row(nodeid, timings):
    """Append one CSV row capturing this test's per-step timings.

    No-op when ``timings`` is ``None`` (profiling disabled) or when
    the flag was unset at module load.  Each row carries the full
    column set in ``_FIXTURE_PROFILE_STEPS`` order so the aggregator
    can read it without per-row schema lookups.
    """
    if timings is None or not _FIXTURE_PROFILE_ENABLED:
        return
    with _FIXTURE_PROFILE_CSV.open("a", newline="", encoding="utf-8") as csv_fp:
        writer = csv.writer(csv_fp)
        writer.writerow([
            nodeid,
            _FIXTURE_PROFILE_WORKER_ID,
            *(f"{timings[step]:.4f}" for step in _FIXTURE_PROFILE_STEPS),
        ])


_profile_session_init()


#: What separates a world SNAPSHOT's name from the worker database it was
#: frozen from (plan step balance:X-be-2).  One constant because three places
#: depend on the split: the name is built with it, the orphan sweep recovers a
#: snapshot's OWNER with it, and the length guard measures across it.
_SNAPSHOT_INFIX = "__world_"


def _like_escape(value):
    """Quote a literal for a ``LIKE`` pattern under ``ESCAPE '\\'``.

    ``_`` and ``%`` are wildcards, and every database name this module builds
    is full of underscores -- so a pattern interpolating one UNESCAPED matches
    names it was never meant to.  ``shekel_test_gw1_%`` matching
    ``shekel_test_gw11`` is that bug, and it predates the snapshots.

    Args:
        value: The literal text to appear in the pattern.

    Returns:
        The text with ``\\``, ``_`` and ``%`` escaped.
    """
    return (
        value.replace("\\", "\\\\").replace("_", "\\_").replace("%", "\\%")
    )


def _bootstrap_worker_database():
    """Create a per-pytest-worker database cloned from the test template.

    Called once at conftest module-load time, BEFORE any ``app``
    import.  Each pytest invocation (and each pytest-xdist worker
    within an invocation) gets its own database; concurrent
    invocations cannot deadlock on the per-test ``TRUNCATE CASCADE``
    because each operates on its own DB.

    Master-vs-worker detection:
        Under pytest-xdist the master process imports conftest for
        test collection but does not run tests.  It sets
        ``PYTEST_XDIST_TESTRUNUID`` but NOT ``PYTEST_XDIST_WORKER``
        (only the workers carry the latter).  The master must skip
        the bootstrap -- otherwise it would leave a per-PID DB that
        nothing uses and is never dropped.  Single-process pytest
        (no ``-n`` flag) has neither variable set and runs the
        bootstrap as ``worker_id="main"``.

    Orphan cleanup:
        On startup the function drops any leftover database that
        matches the worker's name (the Phase 3b stable form
        ``shekel_test_{worker_id}`` AND the legacy PID-suffix form
        ``shekel_test_{worker_id}_*`` from pre-Phase-3b runs) and
        has no active connections in ``pg_stat_activity``.  Handles
        the case where a previous pytest run crashed (SIGKILL,
        kernel OOM, ...) before ``pytest_sessionfinish`` could
        drop its DB.  Filtering by ``pg_stat_activity`` rather than
        name alone defends against the dropping-of-sibling trap: a
        concurrent pytest invocation (rare but documented in
        testing-standards.md) whose worker happens to share this
        worker_id would have its own live DB in the match list;
        the active-connection filter skips dropping it.  CREATE
        DATABASE later in this function will then fail with
        "database already exists" -- the right fail-loud signal
        that two concurrent invocations cannot share a cluster
        under Phase 3b's stable-name scheme.

    Template existence:
        The bootstrap fails fast with an actionable
        ``RuntimeError`` if ``shekel_test_template`` does not
        exist.  The fix is documented in the error message:
        ``python scripts/build_test_template.py``.

    Clone verification:
        After the clone, a fresh psycopg connection counts rows
        in ``ref.account_types``.  Anything other than the
        expected 19 means the template was corrupt at clone time
        and needs to be rebuilt; another actionable error message
        steers the operator to the fix.

    Side effects:
        Sets ``os.environ["TEST_DATABASE_URL"]`` to the per-
        session DSN.  ``app.config.TestConfig`` reads this at
        class-body evaluation time during the next ``from app
        import ...``; the env var write must precede that import.

    Returns:
        ``None`` when bootstrap is skipped (xdist master).
        ``(db_name, admin_url)`` otherwise; ``pytest_sessionfinish``
        uses these to DROP the per-session DB after the suite ends.

    Raises:
        RuntimeError: When the template DB is missing, or when
            the freshly-cloned per-session DB carries a row count
            that disagrees with the seed list size.  Both errors
            include the recovery command in the message.
    """
    # xdist master: TESTRUNUID set, WORKER not set.  Skip entirely
    # so the master process does not create a DB that nothing uses.
    if (os.environ.get("PYTEST_XDIST_TESTRUNUID")
            and not os.environ.get("PYTEST_XDIST_WORKER")):
        return None

    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "main")
    # Phase 3b: stable per-worker DB name (no PID suffix).  Per-test
    # drop+reclone re-uses the SAME name on every test so the
    # Flask-SQLAlchemy engine's URL stays valid across test boundaries
    # -- only the underlying database is swapped atomically by
    # DROP+CREATE.  The PID-bearing form (legacy) leaked into the
    # cluster's DB list whenever a previous run crashed; orphan
    # cleanup below catches both forms.
    db_name = f"{_TEST_DATABASE_PREFIX}_{worker_id}"
    admin_url = os.environ.get(
        "TEST_ADMIN_DATABASE_URL", _DEFAULT_ADMIN_URL
    )

    admin_conn = psycopg.connect(admin_url)
    try:
        admin_conn.autocommit = True
        with admin_conn.cursor() as cur:
            # Orphan cleanup -- match the Phase 3b stable name AND
            # legacy PID-suffix names from pre-Phase-3b runs, then
            # exclude any DB with live connections (a concurrent
            # pytest invocation against the same cluster).
            # ``_`` is a SINGLE-CHARACTER WILDCARD in ``LIKE`` and both halves
            # of these names are full of them, so the separator is ESCAPED.
            # Unescaped, ``shekel_test_gw1_%`` matches ``shekel_test_gw11`` --
            # worker gw1 sweeping gw10's and gw11's databases, which is a real
            # drop whenever the victim sits between tests with its engine
            # disposed, and pytest-xdist restarts a crashed worker (up to
            # ``numprocesses * 4``), so a restart re-runs this sweep mid-session
            # rather than only at startup.  Verified on the cluster:
            # ``'shekel_test_gw11' LIKE 'shekel_test_gw1\_%'`` is false and
            # ``'shekel_test_gw1__world_x' LIKE 'shekel_test_gw1\_%'`` is true.
            #
            # The second pattern collects WORLD SNAPSHOTS left by a run at any
            # ``-n``: a killed ``-n 0`` run leaves ``{prefix}_main__world_*``
            # that no later ``-n 12`` run would otherwise ever name, because
            # the first pattern only ever sweeps the ids the CURRENT
            # invocation happens to use.
            cur.execute(
                "SELECT datname FROM pg_database "
                r"WHERE datname = %s OR datname LIKE %s ESCAPE '\' "
                r"   OR datname LIKE %s ESCAPE '\'",
                (
                    db_name,
                    f"{_like_escape(db_name)}\\_%",
                    f"{_like_escape(_TEST_DATABASE_PREFIX)}\\_%"
                    f"{_like_escape(_SNAPSHOT_INFIX)}%",
                ),
            )
            candidate_orphans = [row[0] for row in cur.fetchall()]
            if candidate_orphans:
                # **A snapshot's own connections say NOTHING about whether it
                # is in use, and that is what makes the filter below a
                # different question for it.**  Nothing ever connects to a
                # snapshot: it is written by ``CREATE DATABASE ... TEMPLATE``
                # and read only AS a template, by an admin-DSN connection to
                # another database.  So it never appears in
                # ``pg_stat_activity``, the filter can never spare it, and
                # before this predicate a second invocation dropped a LIVE
                # run's snapshot -- reproduced twice by review, once as 158
                # setup errors reading ``template database ... does not
                # exist`` a hundred tests after the intruder had gone.
                #
                # A snapshot is alive exactly when its OWNER is, so it
                # inherits the owner's answer.  The owner is the whole name
                # left of ``__world_``, and the same rule covers the family
                # case the sweep's own docstring argues for: while a
                # concurrent run holds ``{db_name}``, none of its children are
                # collected either, and that run's ``CREATE DATABASE`` still
                # fails loudly on itself as it always did.
                cur.execute(
                    "SELECT DISTINCT datname FROM pg_stat_activity "
                    "WHERE datname IS NOT NULL",
                )
                active = {row[0] for row in cur.fetchall()}
                for orphan in candidate_orphans:
                    owner = orphan.split(_SNAPSHOT_INFIX)[0]
                    if orphan in active or owner in active:
                        continue
                    cur.execute(
                        sql.SQL(
                            "DROP DATABASE IF EXISTS {} WITH (FORCE)"
                        ).format(sql.Identifier(orphan))
                    )

            # Template existence -- fail fast with a recovery hint.
            cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (_TEST_TEMPLATE_DATABASE,),
            )
            if cur.fetchone() is None:
                raise RuntimeError(
                    f"Test template database "
                    f"{_TEST_TEMPLATE_DATABASE!r} not found.  Under "
                    "./scripts/test.sh this is unreachable -- the image is "
                    "verified before pytest starts -- so something else "
                    "supplied this cluster.  On a cluster you control: "
                    "python scripts/build_test_template.py"
                )

            # STRATEGY WAL_LOG, not FILE_COPY -- see the measurement
            # table in _clone_worker_database below.  The one-per-
            # session clone here is not itself expensive; it matches
            # the per-test helper so the two cannot drift into
            # producing databases by different mechanisms.
            cur.execute(
                sql.SQL(
                    "CREATE DATABASE {} TEMPLATE {} STRATEGY WAL_LOG"
                ).format(
                    sql.Identifier(db_name),
                    sql.Identifier(_TEST_TEMPLATE_DATABASE),
                )
            )
    finally:
        admin_conn.close()

    # Verify the clone is intact -- a fresh psycopg connection
    # bypasses any SQLAlchemy pool state from the admin connection
    # above.  A row count mismatch means the template itself was
    # corrupt; the message names the fix.
    per_session_url = urlunparse(
        urlparse(admin_url)._replace(path=f"/{db_name}")
    )
    verify_conn = psycopg.connect(per_session_url)
    try:
        with verify_conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ref.account_types")
            account_type_count = cur.fetchone()[0]
            if account_type_count != _EXPECTED_ACCOUNT_TYPE_COUNT:
                raise RuntimeError(
                    f"Per-session DB {db_name!r} appears corrupted "
                    f"(ref.account_types count={account_type_count}, "
                    f"expected {_EXPECTED_ACCOUNT_TYPE_COUNT}).  Under "
                    "./scripts/test.sh the template is baked into an image "
                    "verified before pytest starts, so reaching this means "
                    "the image is stale in a way its own verification missed "
                    "-- rebuild it with "
                    "'python scripts/build_test_db_image.py --force'.  On a "
                    "cluster you supplied: python scripts/build_test_template.py"
                )
    finally:
        verify_conn.close()

    # Point the app's TestConfig at the per-session DB.  Must
    # precede the first ``from app import ...`` below.
    os.environ["TEST_DATABASE_URL"] = per_session_url

    return (db_name, admin_url)


# Execute the bootstrap at module load time.  ``None`` when the xdist
# master skipped; ``pytest_sessionfinish`` keys off this to decide
# whether to drop the per-session DB.
_BOOTSTRAP_RESULT = _bootstrap_worker_database()


# Pull the worker DB name and admin DSN into module-level constants so
# the per-test ``db`` fixture (Phase 3b) can drop+reclone without
# unpacking ``_BOOTSTRAP_RESULT`` on every call.  ``None`` when the
# bootstrap was skipped (xdist master), in which case the per-test
# fixture will refuse to run -- the master never executes tests so
# this branch should be unreachable in practice; the defensive check
# inside the fixture surfaces a clear error if it ever fires.
if _BOOTSTRAP_RESULT is not None:
    _WORKER_DB_NAME, _WORKER_ADMIN_URL = _BOOTSTRAP_RESULT
else:
    _WORKER_DB_NAME = None
    _WORKER_ADMIN_URL = None


def _drop_worker_database(db_name, admin_url):
    """Drop the per-worker test database via an admin psycopg connection.

    Phase 3b helper.  Called once per test by the ``db`` fixture
    (before ``_clone_worker_database`` re-creates it) and at session
    end by ``pytest_sessionfinish``.

    ``WITH (FORCE)`` (PostgreSQL 13+) terminates any leftover backend
    that escaped the previous test's ``_db.engine.dispose()``;
    without it a stuck transaction would block the drop.  Identifier
    interpolation goes through :mod:`psycopg.sql` so the
    ``shekel_test_*`` name stays safely quoted even though it comes
    from a controlled f-string at module load time -- consistent
    with the rest of this module's admin-DSN access pattern.

    Args:
        db_name: Name of the per-worker DB to drop.
        admin_url: Admin DSN (must NOT point at ``db_name`` itself
            -- ``DROP DATABASE`` cannot run against the connection's
            own database).
    """
    admin_conn = psycopg.connect(admin_url)
    try:
        admin_conn.autocommit = True
        with admin_conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "DROP DATABASE IF EXISTS {} WITH (FORCE)"
                ).format(sql.Identifier(db_name))
            )
    finally:
        admin_conn.close()


def _clone_worker_database(db_name, admin_url, template=None):
    """Re-create the per-worker test DB by cloning ``shekel_test_template``.

    Phase 3b helper.  Called once per test by the ``db`` fixture
    (immediately after ``_drop_worker_database``) to give every test
    the same start state the prior TRUNCATE+reseed cycle provided:
    empty ``system.audit_log``, no rows in ``budget.*`` / ``auth.*`` /
    ``salary.*``, full ref-data seed in ``ref.*``.  The template's
    contents come from ``scripts/build_test_template.py``.

    **Why ``STRATEGY WAL_LOG`` and not ``FILE_COPY``.**  This clone
    runs once per TEST, so its cost is multiplied by ~9,000.
    ``FILE_COPY`` makes PostgreSQL force three cluster-wide
    checkpoints per statement (one ``immediate force wait
    flush-all`` before the copy plus one after, and one more for the
    paired ``DROP DATABASE``); ``WAL_LOG`` forces none of its own,
    leaving only the DROP's.  Counted, not assumed: 20 drop+create
    cycles emit 60 forced checkpoints under ``FILE_COPY`` and 20
    under ``WAL_LOG``.

    Those checkpoints are free only on a cluster with durability
    switched off, which is exactly what ``docker-compose.dev.yml``
    gives the dev test-db and what CI's stock postgres service
    container does NOT.  Measured on one host against the real
    12 MB template, 20 drop+create cycles, per cycle:

    ==========================  ===========  ==========
    cluster                     FILE_COPY    WAL_LOG
    ==========================  ===========  ==========
    stock durable (CI's)        1618 ms      80 ms
    ``fsync=off`` (dev's)       31 ms        26 ms
    ==========================  ===========  ==========

    So ``FILE_COPY`` was 20x slower than the default strategy on any
    durable cluster and never faster on a non-durable one, which is
    how CI came to spend 702.8 s of a 1733.55 s run inside a
    single-threaded checkpointer that all 12 xdist workers block on
    -- and why tests carrying a fixed wall-clock budget (the
    ``url_map`` sweep at pytest-timeout's 30 s, the ``seed_user.py``
    subprocesses at 10 s) began failing about half the time from
    2026-08-16.  ``file_copy_method=clone`` does not rescue it: the
    reflink ioctl saves the byte copy, not the checkpoints, and
    measured 1594 ms per cycle on the durable cluster.

    The two strategies differ only in how blocks reach the new
    database, never in what it contains -- verified by diffing
    ``pg_dump`` of a ``FILE_COPY`` clone against a ``WAL_LOG`` clone
    of the same template: identical but for pg_dump's own random
    ``\\restrict`` nonce.  Row IDs, sequences and the
    ``ref.account_types`` count the bootstrap verifies are therefore
    unchanged.

    Args:
        db_name: Name of the per-worker DB to create.
        admin_url: Admin DSN (must NOT point at ``db_name`` itself).
        template: The database to clone FROM.  Defaults to the empty
            ``shekel_test_template``, which is what every test that has not
            declared a seeded start state gets.  A test carrying the
            ``seeded_start_state`` marker passes that world's snapshot instead
            (plan step balance:X-be-2) -- the ISOLATION is identical either
            way, a private database per test; only the content it starts with
            differs.
    """
    admin_conn = psycopg.connect(admin_url)
    try:
        admin_conn.autocommit = True
        with admin_conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "CREATE DATABASE {} TEMPLATE {} STRATEGY WAL_LOG"
                ).format(
                    sql.Identifier(db_name),
                    sql.Identifier(template or _TEST_TEMPLATE_DATABASE),
                )
            )
    finally:
        admin_conn.close()


import pytest
from flask import Flask

from app import create_app, tax_law as app_tax_law
from app.extensions import db as _db
from app.utils.dates import DISPLAY_TIMEZONE, display_today
from app.models.user import User, UserSettings
# ``Account`` is intentionally not imported here: every test fixture
# constructs accounts via ``app.services.account_service.create_account``,
# the canonical post-E-19 factory.  Tests that need to exercise the
# storage-tier NOT NULL constraint directly use raw SQL inserts.
from app.models.scenario import Scenario
from app.models.category import Category
from app.models.salary_profile import SalaryProfile
from app.models.savings_goal import SavingsGoal
from app.models.transfer_template import TransferTemplate
from app.models.ref import (
    AccountType, FilingStatus,
)
from app.enums import BusinessDayShiftEnum
from app.services import (
    account_service,
    pay_period_write,
    pay_rhythm,
)
from app.services.auth_service import hash_password
from app.services.pay_calendar import calendar_for
from tests._test_helpers import (
    figure_source_columns,
    rhythm_of,
    mint_fixture_era,
    bind_db_clock_rewriter,
    create_loan_account,
    insert_trueup_event,
    make_appreciating_account,
    make_every_period_rule,
    make_expense_template,
    make_investment_account,
    open_books_before_the_first_assertion,
    open_owner_calendar,
    start_test_pay_list,
    posted_loan_balance_at,
    rebuild_calendar,
    rebuild_calendar_on,
    generate_row_of,
    settle_day_columns,
    state_template_price,
    step_down_through_the_transfer_column_drop,
)
from tests._per_request_globals import PerRequestGlobalsClient
from tests._shard import SHARD_ENV, apply_shard, parse_shard


# ---------------------------------------------------------------------------
# Named seeded start states (plan step balance:X-be-2)
# ---------------------------------------------------------------------------
# **A test says what world it starts in; it does not build one.**
#
# The suite has always been able to express two start states: EMPTY -- the
# ``shekel_test_template`` clone every test gets -- and "build it yourself",
# a function-scoped fixture writing rows.  Nothing could say *start from a
# prepared world*, so a module whose every test needs the same world built it
# once per test.
#
# **The measurement, stated once and in full here** -- the other two files
# that cite it give the headline and point at this paragraph, because three
# copies of one number is how two of them come to disagree.  Measured on the
# ``url_map`` sweep serially, 2026-08-30 (local dates; the figures it
# supersedes are 2026-08-29): 236 cases at 302 ms of setup for 22 ms of
# requests, the world byte-identical every time.  Setup was 93% of the file --
# but 93% is NOT the removable share, and reading it as one over-claims by
# about 2x.  That setup splits: ~180 ms the module's own seven-account
# fixture, ~50 ms ``seed_user``, ~30 ms the pay calendar, ~40 ms the per-test
# clone, ~10 ms the login.  A world removes the first three.  **The clone
# stays, because the clone is the isolation.**  End to end the file went
# 82.26 s -> 18.73 s, a 77% cut, which takes it from 6.8x the suite's ordinary
# per-test cost to about 1.0x -- it stops being special, which is the whole of
# what was wanted.
#
# **It is not a suite-level speedup and was not measured as one.**  Full suite
# before 11,770 tests in 278.25 s, after 11,781 in 281.64 s: unchanged, since
# this file is ~2% of the suite's work and the expected saving sits inside a
# run-to-run variance measured at 18 s.
#
# What this seam separates is ISOLATION from CONTENT.  The per-test drop and
# re-clone is what keeps tests independent and it does not change here: every
# test still gets its own database and may write to it freely.  What changes is
# only which database it is cloned FROM.  A world is built once per worker into
# a snapshot database, and the marker names it:
#
#     @pytest.mark.seeded_start_state("baseline_less_owner")
#     class TestSomething:
#         ...
#
# **Why a snapshot database rather than a wider fixture scope.**  A
# module- or class-scoped fixture cannot hold this state: every test on an
# xdist worker shares ONE database, so the next test from any OTHER module
# re-clones it mid-module.  Observed under ``-n 12`` on 2026-08-29 with gw0,
# gw10 and gw11 each running two modules interleaved -- measured, not inferred
# from the scheduler's documentation.  ``xdist_group`` pins a group to one
# worker and does not keep other tests off that worker, so it is not the fix
# either.  A snapshot is a different database, so nothing else re-clones it.
#
# **Why it is never persisted between runs.**  A world holds rows dated from
# ``display_today()``, so a snapshot kept across midnight -- or across a
# builder edit -- would be a start state no code describes.  It is built at
# first use and dropped in ``pytest_sessionfinish``; the name carries the
# worker DB as its prefix so the bootstrap's existing orphan sweep collects one
# a SIGKILL left behind.  This is finding **N-385**'s shape (a stored artifact
# whose stamp says current while its content is not), and the answer there is
# the same: do not stamp it harder, do not keep it.
#
# **A world is built inside the ``db`` fixture, so it CANNOT see an autouse
# fixture defined in a SUBDIRECTORY conftest, and that is a real limit rather
# than an oversight.**  ``db`` names the two autouse fixtures in THIS file that
# a builder needs, which orders them ahead of it; it cannot name a fixture
# defined below it in the tree, and those sort after ``db`` unconditionally.
# The live example: ``tests/test_services/conftest.py`` freezes
# ``pay_period_service.date.today()`` to a fixed day for every test under it.
# A world declared there would be BUILT on the real clock and then READ by
# tests that see the frozen one.  No world is declared under such a conftest
# today.  Before adding one, check what its directory's conftest does to the
# environment -- the build will not have had it.
#
# **A test that declares a world may not ALSO request a fixture that seeds the
# same rows.**  ``seed_user`` inside a test whose world already holds that owner
# inserts a second one and PostgreSQL refuses it on the unique email -- loudly,
# at the fixture, which is why this is a note rather than a guard: the failure
# names the collision itself, and a guard would have to enumerate which fixtures
# write what, which is the census this seam exists to stop anyone keeping.  A
# test needing a DIFFERENT owner declares no world (see
# ``TestTheHandlerIsInertForAHealthyOwner`` in the no-baseline sweep) or gets
# its own.
#
# **What a builder may return.**  Plain data only -- ids and scalars.  The
# dict is computed once, at build time, and handed to every test that asks for
# the world; an ORM object in it would be bound to a session that closed with
# the build, and a caller would get a detached row rather than an error.  Row
# ids are safe because ``CREATE DATABASE ... TEMPLATE`` preserves them, which
# is the same property the per-test clone already depends on for ``ref.*``.
_SEEDED_STATE_BUILDERS = {}

# Worlds built in THIS process, ``name -> (snapshot database, ids dict)``.
# Per worker rather than per session because each worker is its own process
# with its own database; two workers running tests from the same module each
# build the world once.
_SEEDED_STATE_SNAPSHOTS = {}

# PostgreSQL truncates an identifier at 63 bytes, so two long world names could
# silently name ONE snapshot and each would then serve the other's tests.  The
# refusal is at REGISTRATION -- a name is a constant, so this fires at import
# for every run, not on the unlucky one that happened to use both worlds.
_MAX_IDENTIFIER_BYTES = 63

# **The guard measures the LONGEST name the scheme can produce, not this
# process's.**  Measuring the running worker's name makes the check
# environment-dependent: on the xdist CONTROLLER, which imports test modules
# but runs no test, ``_WORKER_DB_NAME`` is ``None`` and the probe would
# measure the string ``"None"``.  The bound below is the prefix plus the
# widest worker id xdist can hand out.  It used to take the longer of the
# configured prefix and the default, because ``TEST_DB_PREFIX`` could make
# them differ and a name that fitted locally could be refused in CI; that
# variable is gone (``balance:X-br-4``) and there is one prefix again.
_WIDEST_WORKER_ID = "gw999"
_WIDEST_WORKER_DB = f"{_TEST_DATABASE_PREFIX}_{_WIDEST_WORKER_ID}"


def register_seeded_state(name, builder):
    """Register a named world tests may declare as their start state.

    Called at import time by the module that defines the world, so a test
    carrying the marker has always registered it by the time it runs.

    Args:
        name: The name tests pass to ``@pytest.mark.seeded_start_state``.
        builder: ``builder(db) -> dict`` -- writes the world through
            ``db.session`` and returns plain data (ids, scalars) describing it.
            It must COMMIT nothing it does not want in the snapshot and must
            not depend on the wall clock beyond ``display_today()``.

    Raises:
        ValueError: The name is already registered to a different builder, or
            it is long enough that its snapshot identifier would be truncated.
    """
    existing = _SEEDED_STATE_BUILDERS.get(name)
    if existing is not None and existing is not builder:
        raise ValueError(
            f"seeded start state {name!r} is already registered to "
            f"{existing.__module__}.{existing.__qualname__}. Two worlds under "
            f"one name would share one snapshot database and each would serve "
            f"the other's tests."
        )
    widest = f"{_WIDEST_WORKER_DB}{_SNAPSHOT_INFIX}{name}"
    if len(widest.encode()) > _MAX_IDENTIFIER_BYTES:
        raise ValueError(
            f"seeded start state {name!r} needs a snapshot database named up "
            f"to {widest!r}, which is {len(widest.encode())} bytes -- "
            f"PostgreSQL truncates at {_MAX_IDENTIFIER_BYTES} and two "
            f"truncated names would collide. Shorten the world's name."
        )
    _SEEDED_STATE_BUILDERS[name] = builder


def _seeded_snapshot_name(name):
    """The snapshot database a world is built into on THIS worker.

    Prefixed with the worker database's own name, which is what lets the
    orphan sweep in :func:`_bootstrap_worker_database` recover a snapshot's
    OWNER and answer "is this in use?" by asking about the owner instead.  A
    snapshot can never answer that for itself: nothing ever connects to one.

    Args:
        name: The world's registered name.

    Returns:
        The snapshot database name.
    """
    return f"{_WORKER_DB_NAME}{_SNAPSHOT_INFIX}{name}"


#: What a builder may put in the dict it returns.  Not a style preference:
#: the dict is computed ONCE and handed to every declaring test, so anything
#: with interior state would be shared across tests that each believe they
#: hold their own copy, and an ORM object in it would be bound to a session
#: that closed with the build.
_PLAIN_DATA_TYPES = (int, str, float, bool, Decimal, date, datetime, type(None))


def _plain_data_or_raise(name, ids):
    """Refuse a builder's return value unless it is a flat dict of scalars.

    **Enforced rather than documented**, because the failure it prevents is
    silent: :func:`seeded_state_for` hands out ``dict(ids)``, a SHALLOW copy,
    so a nested list or dict would be one object shared by every test that
    declared the world -- and a test mutating it would be editing what every
    later test sees, with nothing to point at.

    Args:
        name: The world's registered name, for the message.
        ids: Whatever the builder returned.

    Returns:
        *ids*, unchanged, when it is a flat mapping of scalars.

    Raises:
        RuntimeError: It is not.
    """
    if not isinstance(ids, dict):
        raise RuntimeError(
            f"the builder for seeded start state {name!r} returned "
            f"{type(ids).__name__}, not a dict. A world describes itself with "
            f"plain data its declaring tests read by key."
        )
    nested = sorted(
        key for key, value in ids.items()
        if not isinstance(value, _PLAIN_DATA_TYPES)
    )
    if nested:
        raise RuntimeError(
            f"the builder for seeded start state {name!r} returned "
            f"non-scalar values under {nested}. The dict is built once and "
            f"shallow-copied to every declaring test, so anything with "
            f"interior state would be shared between tests that each believe "
            f"they hold their own. Return ids, not rows."
        )
    return ids


def _refuse_a_build_the_environment_is_not_ready_for(name):
    """Refuse to build a world before the fixtures it needs have run.

    A builder is ordinary application code, so it needs the environment an
    ordinary fixture gets.  The ``db`` fixture NAMES the autouse fixtures that
    supply it, which orders them first -- and that enumeration is what this
    checks, because the first build ran without it and ``hash_password``
    refused the suite's own seed password with a message about a data breach,
    naming nothing about ordering.

    **It is a diagnostic, not a guarantee**: it can only name the two the
    ``db`` fixture already enumerates, and it cannot see an autouse fixture in
    a SUBDIRECTORY conftest -- those sort after ``db`` and no dependency in
    this file can reach them.  See the block comment above.

    Args:
        name: The world's registered name, for the message.

    Raises:
        RuntimeError: The environment is not the one a fixture would see.
    """
    missing = []
    if os.environ.get("HIBP_CHECK_ENABLED") != "false":
        missing.append("disable_hibp_check")
    if not os.environ.get("TOTP_ENCRYPTION_KEY"):
        missing.append("set_totp_key")
    if missing:
        raise RuntimeError(
            f"the world {name!r} is being built before {' and '.join(missing)} "
            f"has run, so its builder would see an environment no ordinary "
            f"fixture sees. The `db` fixture must DEPEND on every autouse "
            f"fixture a builder needs -- pytest orders fixtures defined in one "
            f"module by name, so coexisting with them is not enough."
        )


def _build_seeded_snapshot(name, app):
    """Build a world once and freeze it into a snapshot database.

    Runs on the first test that asks for *name* on this worker.  **It makes
    the database blank itself, below, and that drop-and-clone is load-bearing
    rather than redundant**: the caller resolves the clone source BEFORE its
    own drop, so the worker database standing here still holds the previous
    test's rows.  Deleting those two lines would freeze whatever that test
    wrote into the world, and every declaring test on this worker would then
    start from it -- silently, differently per worker, since which test ran
    before depends on scheduling.  (An earlier draft of this docstring claimed
    the ``db`` fixture had already blanked it.  It had not.)

    The result is frozen with ``CREATE DATABASE ... TEMPLATE``, which is
    refused while any session holds the source -- hence the dispose before it.

    ``system.audit_log`` is truncated after the build for the reason
    ``scripts/build_test_template.py`` truncates it after seeding the template:
    the rows a SEED writes are not a test's to assert on, and a world that
    shipped them would silently change the meaning of every ``audit_log`` count
    in a test that adopts it.

    Args:
        name: The world's registered name.
        app: The application whose context the builder writes through.

    Returns:
        ``(snapshot_database_name, ids)``.

    Raises:
        KeyError: No builder is registered under *name*.
        RuntimeError: The environment a builder needs is not in place yet, or
            the builder returned something other than plain data.
    """
    try:
        builder = _SEEDED_STATE_BUILDERS[name]
    except KeyError:
        raise KeyError(
            f"no seeded start state named {name!r} is registered. The module "
            f"defining the world must call register_seeded_state({name!r}, ...) "
            f"at import time; known worlds: {sorted(_SEEDED_STATE_BUILDERS)}"
        ) from None

    _refuse_a_build_the_environment_is_not_ready_for(name)
    snapshot = _seeded_snapshot_name(name)
    # See the docstring: this is what makes the database blank, and it is not
    # redundant with the caller's own drop.
    _drop_worker_database(_WORKER_DB_NAME, _WORKER_ADMIN_URL)
    _clone_worker_database(_WORKER_DB_NAME, _WORKER_ADMIN_URL)
    with app.app_context():
        # ``try``/``finally`` so a builder that RAISES still releases this
        # nested context's session.  Flask-SQLAlchemy scopes a session to the
        # app context, so the one opened here is not the ``db`` fixture's; a
        # raise that popped the context without removing it would leave a
        # session holding an aborted transaction, and the connection it holds
        # is exactly what the freeze below is refused for.
        try:
            _refresh_ref_cache_and_jinja_globals(app)
            ids = _plain_data_or_raise(name, builder(_db))
            # The seed's own audit rows are not a test's to see -- same
            # contract, and same reason, as the template builder's post-seed
            # TRUNCATE.
            _db.session.execute(_db.text("TRUNCATE system.audit_log"))
            _db.session.commit()
        finally:
            # ``CREATE DATABASE ... TEMPLATE src`` is refused while any session
            # holds ``src``; releasing the engine here is what makes the freeze
            # below legal, exactly as it is for the per-test DROP.
            _db.session.remove()
            _db.engine.dispose()

    _drop_worker_database(snapshot, _WORKER_ADMIN_URL)
    admin_conn = psycopg.connect(_WORKER_ADMIN_URL)
    try:
        admin_conn.autocommit = True
        with admin_conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "CREATE DATABASE {} TEMPLATE {} STRATEGY WAL_LOG"
                ).format(
                    sql.Identifier(snapshot),
                    sql.Identifier(_WORKER_DB_NAME),
                )
            )
    finally:
        admin_conn.close()
    return snapshot, ids


def declared_seeded_state(request):
    """The world *request*'s test DECLARED, or ``None``.  Builds nothing.

    Split from :func:`seeded_state_for` because that one BUILDS on a miss, and
    building drops and re-clones the worker database -- which is correct while
    the ``db`` fixture is setting a test up, and destroys the database out from
    under the test anywhere else.  Every reader that only wants to know what
    was declared calls this instead, so the destructive path has exactly one
    caller.

    Args:
        request: The test's pytest request.

    Returns:
        The declared world's name, or ``None`` when the test declared none.

    Raises:
        ValueError: The marker is present but names no world.
    """
    marker = request.node.get_closest_marker("seeded_start_state")
    if marker is None:
        return None
    if not marker.args:
        # A marker written without its argument is an ordinary typo, and
        # ``marker.args[0]`` would report it as an IndexError raised inside
        # this file -- naming neither the test nor the thing it forgot.
        raise ValueError(
            f"{request.node.nodeid} carries @pytest.mark.seeded_start_state "
            f"with no world named. Pass one: "
            f"@pytest.mark.seeded_start_state(\"<name>\"); known worlds: "
            f"{sorted(_SEEDED_STATE_BUILDERS)}"
        )
    return marker.args[0]


def seeded_state_for(request, app):
    """The world *request*'s test declared, BUILDING it if this worker has none.

    **Call this only while a test's database is being replaced anyway.**  On a
    miss it drops and re-clones the worker database (see
    :func:`_build_seeded_snapshot`), which is why the ``db`` fixture is its
    only caller; anywhere else that would destroy the running test's database.
    :func:`declared_seeded_state` is the non-destructive question.

    Args:
        request: The test's pytest request, carrying the
            ``seeded_start_state`` marker.
        app: The application whose context a first build writes through.

    Returns:
        ``(clone_source_database, ids)`` -- the database the test's own copy is
        cloned from, and the plain-data description of the world in it.  Both
        are ``(_TEST_TEMPLATE_DATABASE, None)`` for an unmarked test, which is
        every test that has not adopted this seam.
    """
    name = declared_seeded_state(request)
    if name is None:
        return _TEST_TEMPLATE_DATABASE, None
    if name not in _SEEDED_STATE_SNAPSHOTS:
        _SEEDED_STATE_SNAPSHOTS[name] = _build_seeded_snapshot(name, app)
    snapshot, ids = _SEEDED_STATE_SNAPSHOTS[name]
    # A copy per test: the dict is built once and shared, and a test that
    # mutated it would be editing every later test's view of the world.
    # ``_plain_data_or_raise`` is what makes a SHALLOW copy sufficient.
    return snapshot, dict(ids)


def seeded_state_ids(name):
    """The ids of a world already built on this worker.

    Args:
        name: The world's registered name.

    Returns:
        A per-test copy of the builder's dict.

    Raises:
        KeyError: The world has not been built in this process.
    """
    _snapshot, ids = _SEEDED_STATE_SNAPSHOTS[name]
    return dict(ids)


def _drop_seeded_snapshots():
    """Drop every world this worker built.  Called from session finish.

    A world's rows are dated from ``display_today()``, so a snapshot that
    outlived its session would be a start state no code describes.
    """
    if not _SEEDED_STATE_SNAPSHOTS:
        return
    for snapshot, _ids in _SEEDED_STATE_SNAPSHOTS.values():
        _drop_worker_database(snapshot, _WORKER_ADMIN_URL)
    _SEEDED_STATE_SNAPSHOTS.clear()


# --- App & DB Fixtures ---------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def fast_bcrypt():
    """Use minimum bcrypt rounds (4) for all tests.

    Bcrypt's default work factor (12) makes each hash take ~250ms.
    Rounds=4 reduces this to ~2ms, saving 10+ seconds across the
    full suite without affecting test correctness.
    """
    import bcrypt as _bcrypt  # pylint: disable=import-outside-toplevel
    _original_gensalt = _bcrypt.gensalt

    def _fast_gensalt(rounds=4, prefix=b"2b"):
        """Generate a bcrypt salt with minimum work factor."""
        return _original_gensalt(rounds=rounds, prefix=prefix)

    _bcrypt.gensalt = _fast_gensalt
    yield
    _bcrypt.gensalt = _original_gensalt


@pytest.fixture(autouse=True)
def set_totp_key(monkeypatch):
    """Set a test TOTP encryption key for all tests."""
    from cryptography.fernet import Fernet  # pylint: disable=import-outside-toplevel
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.fixture(autouse=True)
def disable_hibp_check(monkeypatch):
    """Disable the HIBP breached-password check by default.

    ``hash_password`` is invoked from dozens of fixtures (every
    ``seed_user`` variant, plus per-test registration helpers) and
    making each one perform an outbound HTTP call would (a) break the
    suite's hermeticity, (b) slow it by an order of magnitude, and
    (c) silently mask test results during HIBP outages.

    Tests that exercise HIBP behaviour explicitly flip this back on
    via ``monkeypatch.setenv("HIBP_CHECK_ENABLED", "true")`` after
    mocking ``requests.get``.  ``monkeypatch`` is function-scoped so
    the override is local to a single test even when the autouse
    fixture has already run.

    See audit finding F-086 / commit C-11 for the production posture
    (default-on) and ``app/services/auth_service.py:_check_pwned_password``
    for the runtime read.
    """
    monkeypatch.setenv("HIBP_CHECK_ENABLED", "false")


@pytest.fixture(scope="session", autouse=True)
def _calendar_sweep():
    """Run the whole session as if today were ``SHEKEL_FAKE_TODAY``.

    **Opt-in, and a no-op unless the variable is set**, so an ordinary run is
    byte-identical to one without this fixture.

    Four separate defects in this suite had exactly one trigger: the day of the
    month the suite happened to run on (findings N-131, N-132, R8, and the two
    loan cross-surface tests that failed on 2026-08-01).  Each was found by a
    merge gate rather than by a test, because nothing here could ask "does this
    still pass on the 1st?".  This is that instrument::

        SHEKEL_FAKE_TODAY=2026-09-01 ./scripts/test.sh

    ``tick=True`` keeps the clock RUNNING from the faked instant rather than
    freezing it, because a frozen clock makes every ``created_at`` in a session
    identical and the fold's assertion ordering (ruling R-DH: two assertions
    sharing a civil day apply in recording order) then has no order to read.

    **What it can and cannot move, stated because the gap decides how to read a
    failure.**  It moves the PYTHON clock -- ``date.today()``,
    ``datetime.now()`` and therefore :func:`app.utils.dates.display_today`.  It
    does NOT move POSTGRES: ``created_at`` / ``updated_at`` are
    ``server_default=db.func.now()`` (``app/models/mixins.py:237-263``),
    evaluated in the database.  (The settle day was a fourth such reach until
    plan step X-f1; the seam stamps ``display_today()`` into a ``DATE`` column
    now, which this instrument DOES move.)  So under a fake date every
    server-stamped row
    carries the REAL instant, and a test comparing a fixture-built date against
    a server-stamped one fails by the offset between them.  That failure is an
    artifact of this instrument, not a defect in the test -- see
    ``docs/testing-standards.md`` for how to tell the two apart.

    The instant is built in ``DISPLAY_TIMEZONE`` at midday, so the faked civil
    day is unambiguous in the user's zone and cannot straddle midnight in either
    direction.
    """
    fake = os.environ.get("SHEKEL_FAKE_TODAY")
    if not fake:
        yield
        return

    # Pylint: ``import-outside-toplevel`` -- a test-only dependency imported
    # only when the sweep is switched on, so an ordinary run never loads it.
    # pylint: disable=import-outside-toplevel
    import time_machine

    target = datetime.combine(
        date.fromisoformat(fake), dt_time(12, 0), tzinfo=DISPLAY_TIMEZONE,
    )
    with time_machine.travel(target, tick=True):
        yield


@pytest.fixture(scope="session", autouse=True)
def each_request_has_its_own_g():
    """Give every test-client request a ``flask.g`` of its own, as production does.

    The ``db`` fixture below runs each test inside ONE application context,
    and a test-client request REUSES that context -- and its ``g`` -- rather
    than pushing its own.  Flask-Login caches the signed-in user on ``g``, so
    before this, once any request had cached one, every later request in the
    test was answered as that user, whichever client sent it (ledger row
    ``balance:BAL-521``, plan step ``balance:X-cr``).
    :class:`tests._per_request_globals.PerRequestGlobalsClient` gives each
    request a fresh ``g`` and puts the test's back afterwards; its module
    docstring carries the measurement and the design.

    **Set on the ``Flask`` CLASS, not on the session's app**, because the
    suite builds more apps than that one: a test that calls
    ``create_app("testing")`` itself and requests inside its own
    ``with that_app.app_context():`` shares a ``g`` across those requests in
    exactly the same way.  Session-scoped and autouse so it is in place before
    any fixture or test makes a client, and undone when the session ends.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Flask, "test_client_class", PerRequestGlobalsClient)
        yield


@pytest.fixture(scope="session")
def app():
    """Create the Flask application configured for testing."""
    application = create_app("testing")
    yield application


@pytest.fixture(scope="session", autouse=True)
def setup_database(app):
    """One-time per-session prep: refresh the in-process ref cache.

    The per-session PostgreSQL database was cloned from
    ``shekel_test_template`` at conftest module-load time (see
    :func:`_bootstrap_worker_database`).  Schemas, tables, audit
    infrastructure, indexes, and reference seed data are therefore
    already present in the database when this fixture runs; the only
    Python-side initialisation remaining is the in-process ref_cache
    and the Jinja globals that mirror the seeded IDs (the templates
    read these at render time -- a missing entry would break every
    page that references one).

    Database teardown happens in :func:`pytest_sessionfinish` at the
    bottom of this module: ``DROP DATABASE ... WITH FORCE`` removes
    the whole per-session DB rather than table-by-table -- faster
    and less brittle than the previous ``drop_all`` + per-schema
    cascade.

    It also binds the frozen-clock statement rewriter to the engine
    (finding N-65, balance plan step X-h).  **Here, and not lazily when a
    test first flushes**: binding on first flush made the rewriter's
    installation depend on some earlier test in the same worker process
    having flushed ORM state under a frozen clock, so a frozen test whose
    only writes were bulk ``query.update(...)`` silently got the real wall
    clock.  Same test, same assertion, opposite result depending on
    scheduling -- a fail-OPEN gate.  The listener is inert until a test
    freezes, so binding it before any test runs costs nothing and removes
    the order dependence.
    """
    with app.app_context():
        _refresh_ref_cache_and_jinja_globals(app)
        bind_db_clock_rewriter(_db.engine)
    yield


@pytest.fixture(autouse=True)
def db(app, setup_database, request, set_totp_key, disable_hibp_check):  # pylint: disable=unused-argument
    """Provide a freshly-cloned database for each test.

    **It DEPENDS on the two environment fixtures rather than merely coexisting
    with them** (plan step balance:X-be-2).  All three are autouse and
    function-scoped, so every test got them either way -- but pytest builds
    the fixture list from ``dir(module)``, which is ALPHABETICAL, and
    ``"db" < "disable_hibp_check" < "set_totp_key"``.  That is the whole of
    why this one used to run first: not scope, and nothing about what it does.
    Harmless while it issued nothing but DDL; it can now run a seeded start
    state's builder, which is ordinary application code, and the first attempt
    built its owner with the HIBP check still live -- ``hash_password``
    refused the suite's own seed password with a message about a data breach.

    **So the dependency edge is the only thing pinning this order**, and
    renaming any of the three would otherwise silently re-sort them.  The
    edge is what makes the ordering a stated requirement rather than an
    alphabetical accident, and ``_refuse_a_build_the_environment_is_not_ready_for``
    is what says so out loud if the next builder needs a fixture nobody added
    here.

    Drops the per-worker DB and re-clones it from
    ``shekel_test_template`` via
    ``CREATE DATABASE ... TEMPLATE ... STRATEGY WAL_LOG``
    (Phase 3b of test-performance-implementation-plan.md).  Each
    test gets bit-for-bit the same start state the prior
    TRUNCATE+reseed cycle provided:

      * ``system.audit_log`` empty -- the template carries zero
        rows by construction; ``scripts/build_test_template.py``
        truncates the log after the seed commits.
      * No rows in ``budget.*`` / ``auth.*`` / ``salary.*`` -- the
        template is freshly migrated and seeded with reference
        data only.
      * Full ref-data seed in ``ref.*`` including the 19
        ``ref.account_types`` built-ins.
      * In-process ``ref_cache`` and Jinja globals re-seated to
        match the cloned DB's row IDs (which equal the template's
        IDs because ``CREATE DATABASE TEMPLATE`` preserves them).

    **A test that declares a SEEDED START STATE gets the first, third and
    fourth of those and not the second** (plan step balance:X-be-2): its
    database is cloned from that world's snapshot, so it starts holding the
    rows the world's builder wrote.  ``system.audit_log`` is still empty --
    the build truncates it, for the reason the template builder does -- and
    the ref data and cached ids are still the template's, because the snapshot
    is itself a clone of the template.  **What does NOT change is the
    isolation**: the drop and re-clone below run for every test either way, so
    a test still cannot see another's writes.  Only the database it is cloned
    FROM differs.

    Mechanism, in order:

      1. ``setup_rollback`` -- defensive ``session.rollback()`` in
         case a prior test left a stale transaction.  Empirically
         a no-op (Phase 0 measured ~0.0 ms).
      2. Release the engine: ``session.remove()`` detaches the
         scoped session; ``engine.dispose()`` closes every pooled
         connection.  Prerequisites for ``DROP DATABASE WITH
         (FORCE)`` -- the FORCE clause severs leftover backends at
         the protocol level, but disposing here avoids the race
         and keeps the engine pool aligned with the freshly-cloned
         DB on the next session access.  Untimed because the
         steady-state cost is ~0 ms (the previous test's teardown
         already disposed).
      3. ``setup_drop_db`` -- admin-DSN ``DROP DATABASE IF EXISTS
         {worker_db} WITH (FORCE)``.
      4. ``setup_clone_template`` -- admin-DSN ``CREATE DATABASE
         {worker_db} TEMPLATE shekel_test_template STRATEGY
         WAL_LOG``.  See ``_clone_worker_database`` for why the
         strategy is named explicitly and what ``FILE_COPY`` cost.
      5. ``setup_refresh_ref_cache`` -- re-seat the in-process
         ref_cache and Jinja globals against the cloned DB.  The
         row IDs are identical to the template's (CLONE preserves
         them), but reseating costs ~5-7 ms and covers the edge
         case where a future migration changes the seeded ID set
         without anyone updating the in-process cache eagerly.

    Why the worker DB name is stable across the session: the URL
    the Flask-SQLAlchemy engine binds to at app-creation time is
    derived from ``TEST_DATABASE_URL`` set by
    ``_bootstrap_worker_database``; that URL remains valid across
    every drop+reclone because only the underlying database is
    swapped, never the URL.  ``engine.dispose()`` between tests
    forces the pool to reconnect on the next session access, and
    the connection re-establishes against the cloned DB at the
    same URL.

    The ``_profile_step`` wrappers below are no-ops when
    ``SHEKEL_TEST_FIXTURE_PROFILE`` is unset; when set they capture
    per-step elapsed time for the Phase 0 harness (see the block
    comment near ``_FIXTURE_PROFILE_ENABLED`` at the top of this
    module).
    """
    if _WORKER_DB_NAME is None or _WORKER_ADMIN_URL is None:
        raise RuntimeError(
            "db fixture invoked from a process that skipped "
            "_bootstrap_worker_database (xdist master?).  The "
            "master should not run tests; check pytest-xdist's "
            "scheduling configuration."
        )

    timings = _profile_new_timings()
    # nodeid is only used by the profile CSV writer; skip the
    # attribute lookup when profiling is disabled so the default
    # path adds zero work beyond the existing fixture body.
    nodeid = request.node.nodeid if timings is not None else None

    with app.app_context():
        with _profile_step(timings, "setup_rollback"):
            # Clear any stale transaction state from a prior test
            # that raised an exception without committing or
            # rolling back.  Defensive; empirically a no-op since
            # the previous teardown's session.remove() detaches
            # any session and engine.dispose() closes its pool.
            _db.session.rollback()

        # Release the engine fully so the DROP below cannot race a
        # held connection.  session.remove() detaches the scoped
        # session; engine.dispose() closes every pooled connection.
        # Untimed -- the work is essentially constant and dominated
        # by Python overhead, not DB round-trips; folding it into
        # setup_drop_db's timer would blur the DROP measurement.
        _db.session.remove()
        _db.engine.dispose()

        # WHICH database this test is cloned from -- the empty template, or
        # the snapshot of a world it declared (plan step balance:X-be-2).
        # Resolved BEFORE the drop because building a world for the first time
        # on this worker needs a blank worker database of its own, and it takes
        # the one it is standing in; resolving after the clone would throw that
        # clone away.  The engine is already released above, which is what lets
        # the build freeze the worker database into a snapshot.
        clone_source, _ = seeded_state_for(request, app)

        with _profile_step(timings, "setup_drop_db"):
            _drop_worker_database(_WORKER_DB_NAME, _WORKER_ADMIN_URL)

        with _profile_step(timings, "setup_clone_template"):
            _clone_worker_database(
                _WORKER_DB_NAME, _WORKER_ADMIN_URL, clone_source,
            )

        with _profile_step(timings, "setup_refresh_ref_cache"):
            # Re-seat the in-process ref_cache and Jinja globals
            # against the cloned DB.  The cloned IDs equal the
            # template IDs (CREATE DATABASE TEMPLATE preserves
            # them) so the cache is normally a no-op refresh, but
            # the explicit reseat covers the edge case where a
            # future migration changes the seeded ID set and an
            # unaware test would otherwise hit a Jinja Undefined.
            # First access to _db.session here triggers a fresh
            # pool connection to the cloned DB at the (unchanged)
            # URL the engine has been bound to since app-create.
            _refresh_ref_cache_and_jinja_globals(app)

        # ``try``/``finally`` so the teardown timer and the CSV row
        # write both run even when the test raises -- a profile
        # harness that silently dropped rows for failing tests would
        # bias the summary toward the passing path.  The outer
        # ``with _profile_step(..., "call")`` captures the elapsed
        # time of the ``yield _db`` (i.e. the test body itself);
        # context manager exit fires after pytest sends back to
        # this generator, so the timer covers the test exactly.
        try:
            with _profile_step(timings, "call"):
                yield _db
        finally:
            with _profile_step(timings, "teardown"):
                # Clean up after each test: detach the scoped
                # session and close the engine pool so the next
                # test's DROP DATABASE has a clean slate.
                # Belt-and-braces with the WITH (FORCE) in
                # _drop_worker_database -- one or the other would
                # suffice, but both together make the per-test
                # contract impossible to violate via a leaked
                # connection.
                _db.session.remove()
                _db.engine.dispose()
            _profile_write_row(nodeid, timings)


@pytest.fixture()
def below_the_transfer_column_drop(db):
    """The case's database, stepped down through revisions ``1712c506ee99`` and ``c956c1d83108``.

    For a migration case of a revision below plan step ``balance:X-bi-6-5``'s
    column drop, requested as ``pytest.mark.usefixtures``;
    :func:`tests._test_helpers.step_down_through_the_transfer_column_drop`
    says what it restores and why it does not come back up.
    """
    step_down_through_the_transfer_column_drop(db.session)


@pytest.fixture()
def owner_client(client):
    """A client logged in as the seeded owner a WORLD already contains.

    The counterpart of ``auth_client`` for a test that declares a seeded start
    state: the owner is already in that world, so seeding a second one would
    collide on the unique email.  Both fixtures log in through the same
    :func:`log_in_seed_user`, so neither can drift into posting credentials
    the other does not write.

    Used on an ordinary test that declares no world, the login simply fails
    its assertion -- loud and immediate, naming the missing owner.

    Returns:
        The logged-in test client.
    """
    return log_in_seed_user(client)


@pytest.fixture()
def seeded_world(request, app, db):  # pylint: disable=unused-argument
    """The world this test declared, as the plain data its builder returned.

    Depends on ``db`` so it can only be read after this test's own copy of the
    world exists; the lookup itself is the cached one the ``db`` fixture
    already performed, so asking twice costs nothing.

    Returns:
        A per-test copy of the builder's dict -- ids and scalars.

    Raises:
        LookupError: The test asked for a world without declaring one.  Not a
            defaulted empty dict: a test reading ids out of a world it never
            asked for would get ``KeyError`` on a name that IS registered, and
            the marker it is missing would be nowhere in that traceback.
    """
    name = declared_seeded_state(request)
    if name is None:
        raise LookupError(
            f"{request.node.nodeid} asked for `seeded_world` without "
            f"declaring one. Add @pytest.mark.seeded_start_state(\"<name>\") "
            f"to the test or its class; known worlds: "
            f"{sorted(_SEEDED_STATE_BUILDERS)}"
        )
    return seeded_state_ids(name)


@pytest.fixture()
def client(app, db):
    """Provide a Flask test client."""
    return app.test_client()


# --- Data Fixtures --------------------------------------------------------


@pytest.fixture()
def bare_user(app, db):
    """A minimal user fixture: User + UserSettings only.

    No account, no pay periods, no scenario.  Tests that exercise
    services in isolation (e.g. ``pay_period_service``) and want a
    clean user state with no pre-existing pay periods use this
    instead of ``seed_user``, which after E-19 / Commit 3 must seed
    a bootstrap period so its default account satisfies the NOT NULL
    anchor columns.

    Returns:
        dict with keys: user, settings.
    """
    user = User(
        email="bare@shekel.local",
        password_hash=hash_password("barepass-12345"),
        display_name="Bare User",
    )
    db.session.add(user)
    db.session.flush()

    settings = UserSettings(user_id=user.id)
    db.session.add(settings)
    db.session.commit()

    return {"user": user, "settings": settings}


@pytest.fixture()
def bare_auth_client(app, db, client, bare_user):  # pylint: disable=unused-argument
    """Provide an authenticated test client for the bare user.

    Companion to ``auth_client``: bare_user has no account, no
    scenario, no categories, and (importantly for pay_period_service
    tests) no bootstrap pay period.  Use this in tests that need a
    logged-in session against a user with no pre-existing financial
    data.
    """
    resp = client.post("/login", data={
        "email": "bare@shekel.local",
        "password": "barepass-12345",
    })
    assert resp.status_code == 302, (
        f"bare_auth_client login failed with status {resp.status_code}"
    )
    return client


@pytest.fixture()
def bare_user_with_cadence(db, bare_user):
    """``bare_user`` plus the cadence row every payday-holding owner has.

    **For the cases that must hand-build a CORRUPT pay period** -- a duplicate
    ``period_index``, an index out of calendar order, a gap, an overlapping
    span.  Those shapes exist to prove a checker or a migration pre-flight
    catches them, and no application door can produce any of them, so
    ``_test_helpers.open_owner_calendar`` is not the answer there the way plan
    step ``pay_calendar:C4-b-1`` made it the answer for ordinary owners.

    What such a case still needs is the half of the owner that is NOT corrupt:
    since plan step ``pay_calendar:C4-b-2``, ``fk_pay_periods_schedule``
    refuses a pay period whose owner holds no ``budget.pay_schedule`` row, so
    ``bare_user`` alone can no longer carry one.  This fixture is that rule
    stated ONCE rather than a schedule-and-era line copied into each case --
    four of them today, and the fifth would be the one that copies it wrongly.
    The era's day is inert for hand-built rows at plan step
    ``pay_calendar:C17-a`` (``mint_fixture_era`` says why).

    The cadence is 14 and no case should assert on it: these owners' periods
    are written by hand and are deliberately inconsistent with any cadence.
    It exists to satisfy the key, not to describe the rows.

    Returns:
        ``bare_user``'s own dict, unchanged -- the schedule row is a side
        effect on the database, not a new key, so a case can swap this fixture
        in for ``bare_user`` without touching anything else it reads.
    """
    mint_fixture_era(bare_user["user"].id, date(2026, 1, 2), 14)
    db.session.commit()
    return bare_user


@pytest.fixture()
def bare_periods(app, db, bare_user):
    """Generate 10 pay periods for ``bare_user`` starting 2026-01-02.

    The bare_user-companion to ``seed_periods`` for tests that test
    the pay-period service contract in isolation: bare_user has no
    pre-existing periods, so the generated periods take indices
    0..9, exactly as the pre-Commit-3 ``seed_periods`` did against
    seed_user.

    Returns:
        List of PayPeriod objects.
    """

    periods = pay_period_write.record_paydays(
        user_id=bare_user["user"].id,
        first_payday=date(2026, 1, 2),
        num_periods=10,
        rhythm=rhythm_of(14),
    )
    db.session.commit()
    return periods


#: The seeded owner's credentials, in ONE place: :func:`build_seed_user`
#: writes them and :func:`log_in_seed_user` posts them, and a fixture that
#: seeded one email while the login form posted another would fail as a 401
#: naming neither.
SEED_USER_EMAIL = "test@shekel.local"
SEED_USER_PASSWORD = "testpass"

#: The day the seeded owner's BOOTSTRAP pay period starts, which is also the
#: day their Checking account's origination assertion is dated for and one day
#: after the day its books open.
#:
#: **Named because it is now a fixture CONTRACT rather than an implementation
#: detail** (plan step X-f3c-2c).  ``budget.account_anchor_history`` is
#: append-only, so no fixture can re-home that assertion onto whatever calendar
#: a periods fixture builds: the seeded account asserts here and only here
#: until a test says otherwise.  A case that turns on which day was last
#: asserted therefore either states its own (see
#: ``_test_helpers.reassert_balance_on``) or names this one.  *This named
#: ``_drop_seed_user_bootstrap`` as what could no longer re-home it; plan step
#: ``pay_calendar:C4-b-1`` deleted that function for the door it was
#: re-implementing, and the property is the append-only table's rather than any
#: one caller's.*
SEED_USER_BOOTSTRAP_START = date(2024, 1, 5)

#: The cadence the seeded owner's schedule runs at, in days.
#:
#: Named beside the opening payday because plan step ``pay_calendar:C4-b-1``
#: made it a STORED fact rather than an inferred one: the bootstrap batch goes
#: through ``pay_period_write.record_paydays``, which upserts the owner's
#: ``budget.pay_schedule`` row at whatever this says (the cadence rule, plan
#: step C3-b).  It was the literal ``13`` in a ``timedelta`` before that -- a
#: period LENGTH standing in for the cadence it derives from -- and the two
#: differ by one, which is exactly the off-by-one a fixture computing a derived
#: column by hand is free to make.
SEED_USER_CADENCE_DAYS = 14


def log_in_seed_user(client):
    """Log *client* in as the seeded owner through the real login form.

    Split out of ``auth_client`` because a test starting from a seeded start
    state already HAS the owner and must not seed a second one, but still
    needs the same session cookie the form issues -- so the two share this
    rather than posting their own copy of the credentials.

    Args:
        client: A Flask test client.

    Returns:
        The same client, now carrying a logged-in session.
    """
    resp = client.post("/login", data={
        "email": SEED_USER_EMAIL,
        "password": SEED_USER_PASSWORD,
    })
    assert resp.status_code == 302, (
        f"login as {SEED_USER_EMAIL} failed with status {resp.status_code}"
    )
    return client


def build_seed_user(db):
    """Create and return a test user with settings, account, and scenario.

    The ``seed_user`` fixture is a one-line call to this, and so is any seeded
    start state that needs the same owner (plan step balance:X-be-2).  It is a
    function rather than only a fixture so those two describe the SAME user
    instead of two definitions that drift -- a world built from a copy of this
    would be a second answer to "what does a seeded owner look like".

    Args:
        db: The Flask-SQLAlchemy extension to write through.

    Returns:
        dict with keys: user, settings, account, scenario, categories,
        bootstrap_period.  ORM objects, live in the caller's session.
    """
    user = User(
        email=SEED_USER_EMAIL,
        password_hash=hash_password(SEED_USER_PASSWORD),
        display_name="Test User",
    )
    db.session.add(user)
    db.session.flush()

    settings = UserSettings(user_id=user.id)
    db.session.add(settings)

    # The owner's OPENING pay period, written by the door that owns the
    # table (plan step pay_calendar:C4-b-1).  ``create_account`` refuses an
    # owner with no pay period, so one has to exist before the Checking
    # account below; what changed is WHO writes it.  This was a hand-built
    # ``PayPeriod(...)`` -- three columns set by the fixture, two of them
    # (``end_date``, ``period_index``) values the writer DERIVES -- and no
    # ``budget.pay_schedule`` row beside it, which is a state
    # ``pay_period_write.record_paydays`` cannot produce and
    # ``registration_service.register_user`` therefore never produces either.  The
    # seeded owner was consequently the one shape production does not have:
    # paydays with no recorded cadence, pay-calendar finding **P8**, which
    # ``pay_schedule_service.resolve_schedule`` had to carry an inferring
    # fallback for.  Going through the writer makes the seeded owner's
    # calendar the same object production's is.
    #
    # The row it writes is byte-identical to the one this replaced --
    # ``end_date`` is ``start + cadence - 1`` and ``period_index`` is 0 for a
    # single-payday batch -- so nothing dated against it moves.  What is NEW
    # is the ``budget.pay_schedule`` row the same call upserts (the cadence
    # rule, plan step C3-b).
    #
    # The day is an arbitrary Friday well before any test's typical 2026
    # range, so it stays out of the way of date-anchored assertions.  The
    # period is exposed as ``seed_user["bootstrap_period"]`` so tests that
    # create additional accounts inline can anchor them to it.
    bootstrap_period = open_owner_calendar(
        user.id, SEED_USER_BOOTSTRAP_START, cadence_days=SEED_USER_CADENCE_DAYS,
    )[0]

    # Baseline scenario BEFORE the account, matching production
    # registration order (Build-Order Step 5): ``create_account`` posts the
    # opening anchor correction into every scenario, so the baseline must
    # exist first -- the seeded Checking then carries its $1000.00 opening
    # on the posting ledger from t0, exactly like a production account.
    scenario = Scenario(
        user_id=user.id,
        name="Baseline",
        is_baseline=True,
    )
    db.session.add(scenario)
    db.session.flush()

    # Default Checking account via the canonical factory.  Tests that
    # later true-up the anchor produce additional AccountAnchorHistory
    # rows; the factory's origination row is here from t0 so the
    # post-Commit-4 resolver reads a consistent event stream.
    checking_type = (
        db.session.query(AccountType).filter_by(name="Checking").one()
    )
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=checking_type.id,
            name="Checking",
            anchor_balance=Decimal("1000.00"),
            # Day one of the period the account is anchored to -- the
            # production shape, an account opened on day one of its own period
            # (ruling R-DH, plan step 2).  Without it the origination asserts a
            # balance on the WALL-CLOCK day while its ``pay_period_id`` points
            # at the 2024 bootstrap: two clocks on one row, years apart.
            #
            # **It is what makes "and then things happened" say so.**  An
            # assertion is the CLOSING balance for its civil day, so a settle
            # dated that day is INSIDE it.  ``tests/test_services/conftest.py``
            # freezes today, and the ordinary settle idiom dates the row on the
            # user's today -- so an origination left on the
            # frozen clock lands on the very civil day the settles do, and every
            # fixture meaning "an account existed, then money moved" silently
            # became "money moved on the opening's own day".  Those fixtures
            # passed only while the OPENING carried a partition exception
            # (finding N-133 / F1); this is N-132's shape one layer up.
            #
            # Supplied to the factory rather than re-stamped afterwards,
            # because ``create_account`` posts the opening's anchor correction
            # keyed on this day: a later re-stamp would leave the ledger
            # holding a stale key plus its reversal in every seeded database.
            observed_on=bootstrap_period.start_date,
        ),
    )
    # **The BOOKS open the day before that assertion** (plan step X-f3c-2b,
    # ruling **R-HG**).  The paragraph above moved the ORIGINATION off the wall
    # clock so it would not share a civil day with the settles; the books
    # themselves need the same separation for a stronger reason.  An opening
    # equity is the CLOSING balance for its own day, so a movement dated on it
    # is inside the figure and is refused outright -- where an assertion merely
    # absorbs one.  Fixtures settling on ``bootstrap_period.start_date`` are
    # ordinary here, and the opening is the only one of the two that forbids
    # them.
    #
    # Restated rather than supplied, because ``create_account``'s day bound
    # (``resolve_observation_day``) floors ``observed_on`` at the calendar's own
    # first day and would refuse the day before it.  That floor is a rule about
    # ASSERTIONS (ruling R-ER); an opening is not one, which is why production's
    # own repair (migration ``d3b6f1c8a274``) writes the row directly too.  It
    # moves no figure: the origination assertion still clears whatever settled
    # on its own day, so every correction is what it was.
    open_books_before_the_first_assertion(db.session, account)

    # Create default categories.
    categories = []
    for group, item in [
        ("Income", "Salary"),
        ("Home", "Rent"),
        ("Auto", "Car Payment"),
        ("Family", "Groceries"),
        ("Credit Card", "Payback"),
    ]:
        cat = Category(
            user_id=user.id,
            group_name=group,
            item_name=item,
        )
        db.session.add(cat)
        categories.append(cat)
    db.session.flush()

    db.session.commit()

    return {
        "user": user,
        "settings": settings,
        "account": account,
        "scenario": scenario,
        "categories": {c.item_name: c for c in categories},
        "bootstrap_period": bootstrap_period,
    }


@pytest.fixture()
def seed_user(app, db):  # pylint: disable=unused-argument
    """Create and return a test user with settings, account, and scenario.

    Returns:
        dict with keys: user, settings, account, scenario, categories.
    """
    return build_seed_user(db)


@pytest.fixture()
def tax_law(monkeypatch):
    """Return an installer that makes a test price under the tax law it names.

    **The ONE way a test changes the tax law** (ruling salary:R-SAL80, "Real
    law, tests may swap").  Every test prices under :data:`app.tax_law.LAW`,
    the law the app ships, unless it calls ``tax_law(law)``: a test whose
    figures were worked with no tax installs
    ``tests._test_helpers.NO_TAX_LAW``, and one worked on made-up figures
    installs those (the builders beside it), so its expected figures do not
    move when a new year of the real law is published.  The law a test
    installs must list its profiles' state, or the engine refuses them (plan
    step salary:X-at-3); ``EMPTY_TAX_LAW`` lists none and prices no profile.

    The installer replaces the module attribute, which is what the resolver
    (:mod:`app.services.tax_config_service`) and the Settings page read at
    call time; ``monkeypatch`` puts the shipped law back after the test.

    Returns:
        A function taking a :class:`app.tax_law.TaxLaw`, installing it, and
        returning it.
    """
    def install(law):
        monkeypatch.setattr(app_tax_law, "LAW", law)
        return law

    return install


def _reset_seed_calendar(owner, first_payday, num_periods, cadence_days):
    """Rebuild *owner*'s WHOLE pay-period schedule through the production door.

    **Plan step ``pay_calendar:C4-b-1``.**  This replaces
    ``_drop_seed_user_bootstrap``, 135 lines that carried out a schedule reset
    by hand: it re-stated the account's opening, deleted the opening pay period
    with a bare ``query.delete()``, renumbered every survivor with raw SQL
    (``SET period_index = period_index - 1``), and then re-posted the anchor
    corrections that delete had disposed.

    **The application already has a door that does that job**:
    ``pay_period_admin.reset_pay_periods``, the settings-page correction that
    wipes every period, rebuilds the schedule from a new opening payday and
    re-syncs the loan genesis postings and the account anchor corrections onto
    what it built.  It is not "exactly" the hand-rolled sequence -- it does
    strictly MORE, and the extra is one of the three differences below.  Each
    difference was a defect in the hand-rolled version:

    * it renumbered ``period_index`` ITSELF -- a fixture computing by hand the
      derived column this arc exists to delete, in raw SQL the ORM cannot see;
    * it never re-synced LOAN genesis postings, so a world holding a loan would
      have been rebuilt with that loan's opening entries missing;
    * it recorded the new batch BESIDE the surviving opening period and deleted
      that period afterwards, so the new periods were written at indices 1..N
      and pulled back to 0..N-1.  The door retires and records in ONE
      ``record_paydays`` call, so they are 0..N-1 when they are written.

    **It refutes ledger row N-392's diagnosis**, which says the seeded account
    reaches its resting state "through a DELETE of the pay period it was opened
    against, which no production path performs".  ``reset_pay_periods`` deletes
    every pay period the owner has, the account's own opening period included,
    and it is reachable from ``POST /pay-periods/reset``.  What was true is
    narrower: no production path performed the hand-rolled SEQUENCE.

    **The re-statement of the account's opening is deliberately NOT carried
    over, and that is measured rather than argued.**  The deleted helper moved
    the books to ``min(governing.opened_on, new_anchor.start_date - 1 day)`` --
    backward only.  Every owner this file builds opens its books the day before
    :data:`SEED_USER_BOOTSTRAP_START`, and every calendar this function is
    asked for starts later than that, so the ``min`` always chose the day
    already stored: the call could not move a day in any world this file
    builds.  ``TestTheResetDoesNotMoveTheBooks`` in
    ``tests/test_arch/test_the_seeded_calendar_comes_from_the_writer.py`` holds
    that as a property rather than leaving it as this paragraph's claim.

    Args:
        owner: The owner dict a seed fixture returned; ``owner["user"]`` is
            whose schedule is rebuilt.
        first_payday: The opening payday of the rebuilt schedule.
        num_periods: How many periods to build from it.
        cadence_days: Days between them, persisted as the owner's cadence by
            the writer (the cadence rule, plan step C3-b).

    Returns:
        The owner's periods, PAYDAY ascending -- which is ``period_index``
        ascending by construction, and is the spelling that survives the column
        drop at plan step ``pay_calendar:C4-c``.
    """
    # The books bound the opening payday; :func:`rebuild_calendar` refuses one
    # at or before them, which is the door every caller reaches rather than
    # this wrapper alone (ruling ``balance:R-HG``).
    return rebuild_calendar(
        owner["user"].id, first_payday, num_periods, cadence_days,
    )


@pytest.fixture()
def seed_periods(app, db, seed_user):
    """Rebuild the seeded owner's schedule as 10 pay periods from 2026-01-02.

    The owner opens with one period at :data:`SEED_USER_BOOTSTRAP_START`, and
    this RESETS that schedule to the one the test wants through
    :func:`_reset_seed_calendar` -- so the returned periods occupy indices
    0..9 because the writer derived them, not because anything renumbered
    them afterwards.

    Returns:
        List of PayPeriod objects, payday ascending.
    """
    return _reset_seed_calendar(
        seed_user, date(2026, 1, 2), 10, SEED_USER_CADENCE_DAYS,
    )


def _today_relative_start_date():
    """Return start_date that places today in period 4 of a 10-period biweekly run.

    Period 4 is the middle of a 10-period window, leaving 4 historical
    periods and 5 future periods.  The start is aligned to the most
    recent Monday so period boundaries fall on weekdays consistently.
    Used by ``seed_periods_today``-style fixtures so that "which paycheck
    contains today" -- ``PayCalendar.period_containing``, and
    ``pay_period_service.get_current_period`` before pay-calendar plan step
    C2-f3a deleted it -- always answers a real period regardless of the
    wall-clock date.
    """
    today = display_today()
    return today - timedelta(days=today.weekday() + 4 * 14)


def build_periods_today(db, seed_user):
    """Generate 10 biweekly pay periods so today falls in period 4.

    The ``seed_periods_today`` fixture is a one-line call to this, and so is
    any seeded start state needing the same calendar (plan step
    balance:X-be-2), for the reason :func:`build_seed_user` is a function.

    **It reads ``display_today()``, so a caller that freezes it freezes this.**
    A world built from this is therefore built once per SESSION and dropped at
    its end -- see the seeded-start-state block comment above; one kept across
    midnight would place today in a period this no longer builds.

    Args:
        db: The Flask-SQLAlchemy extension.  **Unread since plan step
            ``pay_calendar:C4-b-1``** -- the write goes through
            :func:`_reset_seed_calendar`, which reaches the session itself --
            and kept because it is how the two external callers
            (``test_no_baseline_policy``, the seeded-state builders) declare
            that the ``db`` fixture must be active before this runs.
            **This is the shape ledger row ``balance:N-393`` records as a
            defect** -- an argument its body never reads -- and the reason it
            is kept anyway is the one thing that row's subject does not have: a
            pytest fixture parameter is a DEPENDENCY DECLARATION, so removing
            it would not tidy a signature, it would let a caller run this
            before the database fixture had built a session.  Named rather
            than left for the next reviewer to re-derive.
        seed_user: The owner dict :func:`build_seed_user` returned.

    Returns:
        List of PayPeriod objects, payday ascending.
    """
    return _reset_seed_calendar(
        seed_user, _today_relative_start_date(), 10, SEED_USER_CADENCE_DAYS,
    )


@pytest.fixture()
def seed_periods_today(app, db, seed_user):  # pylint: disable=unused-argument
    """Generate 10 biweekly pay periods so today falls in period 4.

    Use this fixture when the test exercises a code path that asks which
    paycheck contains today (``PayCalendar.period_containing``, directly or
    via a route handler).  Use the regular ``seed_periods`` fixture when the
    test asserts on specific calendar dates (due_date filters,
    year-end summaries for tax_year=2026, loan origination alignment).

    A test must use one or the other, never both -- they would write
    overlapping pay_periods rows for the same user.

    Returns:
        List of PayPeriod objects, ordered by period_index.
    """
    return build_periods_today(db, seed_user)


@pytest.fixture()
def seed_schedule_at_cadence(app, db, seed_user):
    """Return a factory that seeds the owner a schedule at a CHOSEN cadence.

    The parameterised sibling of :func:`seed_periods_today`, which is fixed at
    14 days.  Every other periods fixture in this file is biweekly, so until
    recurrence plan step **R-F17** no test could tell a derivation from the
    hardcoded 26 that stood for "a year" -- which is exactly why ledger row
    **F-17** shipped uncovered.  A test that asserts on a cadence-derived
    window uses this; one that only needs "today is in a paycheck" keeps the
    cheaper fixture.

    Like :func:`seed_periods_today` it RESETS the seeded owner's schedule
    through :func:`_reset_seed_calendar`, so ``period_index`` runs from 0
    because the writer derived it, and it places today in period
    *periods_before* -- **for a cadence of 7 days or more**.  The first payday
    is aligned back to the most recent Monday first, so at a shorter cadence
    that up-to-six-day alignment is itself worth a period or more and today
    lands later than *periods_before*.  No caller passes below 7; the bound is
    stated rather than left as a claim that happens to hold.

    Args:
        app: The Flask app fixture (for the application context the writer
            needs).
        db: The SQLAlchemy ``db`` fixture.
        seed_user: The owner these periods belong to.

    Returns:
        ``_seed(cadence_days, num_periods=40, periods_before=4)`` -> the
        owner's periods, payday ascending.  40 is wide enough that a full year
        of paychecks fits at cadences of 14 days or longer; a weekly case asks
        for more.

        **``periods_before`` used to be bounded by the writer's forward-only
        rule and no longer is** (plan step ``pay_calendar:C4-b-1``).  While this
        fixture APPENDED beside the seeded owner's opening payday,
        ``pay_period_batch.reject_backward_payday`` refused a first payday
        earlier than one full cycle after it -- so four periods of history at a
        300-day cadence started three years before that payday and was refused.
        Going through the reset door retires every existing period in the same
        ``record_paydays`` call that records the new ones, and that refusal
        returns early on an empty surviving set, so a long-cadence case is free
        to ask for as much history as it wants.  The default still matches
        :func:`seed_periods_today`.
    """
    def _seed(cadence_days, num_periods=40, periods_before=4):
        today = display_today()
        first_payday = today - timedelta(
            days=today.weekday() + periods_before * cadence_days,
        )
        return _reset_seed_calendar(
            seed_user, first_payday, num_periods, cadence_days,
        )
    return _seed


@pytest.fixture()
def auth_client(app, db, client, seed_user):  # pylint: disable=unused-argument
    """Provide an authenticated test client.

    Logs in via the login form to get a proper session.
    """
    return log_in_seed_user(client)


def _build_cross_page_calendar_periods(db, user):
    """Build 36 calendar-monthly pay periods for the cross-page lock fixtures.

    The shared period-construction step behind ``seed_cross_page_account``
    and every per-kind cross-page fixture (loan / property / investment /
    secured).  Resets the owner's schedule to one period per calendar month
    spanning ``[today.year - 1, today.year + 1]``, then returns the full
    ordered period list and the anchor period (the calendar month containing
    today).

    Monthly (not biweekly) periods are deliberate: the anchor period's last
    covered day IS a calendar month-end -- each payday is the 1st, so each
    period runs to the day before the next 1st -- and the C9-3 boundary
    invariant of the seam's
    :func:`app.services.balance_at.cash_balance_at` then makes the calendar
    surface's projected month-end balance equal the anchor-period balance for
    the same data.  The anchor being the month containing today also
    lets ``PayCalendar.period_containing`` land on it with no date-mock
    plumbing.

    **It goes through the reset door on a MONTHLY rhythm since plan step
    ``pay_calendar:C17-d-2``** (ledger row **P78**, closed there).  Until
    that step no door could write this schedule -- ``record_paydays`` spaced
    a batch at ONE day count and calendar months are 28 to 31 days apart --
    so the rows were built by hand beside a fixed-days era of 31 that
    disagreed with them (the LAST period's projected end, and every
    horizon a surface derives from the cadence, read a 31-day owner).  The
    day-of-month kind is the door: ``Rhythm(Monthly(1), NONE)`` states the
    calendar these rows always were, the writer records it, and the era
    answers 12 paychecks a year and exactly *n* within *n* months.

    Args:
        db: The SQLAlchemy ``db`` fixture.
        user: The owning :class:`~app.models.user.User` (its ``id`` scopes
            the periods).

    Returns:
        ``(all_periods, anchor_period)`` -- the user's full period list in
        payday order (the 36 monthly periods) and the anchor period
        containing today.
    """
    today = display_today()
    first_year = today.year - 1
    created = rebuild_calendar_on(
        user.id, date(first_year, 1, 1), 36,
        pay_rhythm.Rhythm(pay_rhythm.Monthly(1), BusinessDayShiftEnum.NONE),
    )
    all_periods = created

    # The anchor period is the calendar month containing today, and the
    # containment is ASSERTED through the calendar rather than off the row:
    # plan step ``pay_calendar:C4-c`` dropped ``end_date``, and this fixture's
    # whole point is that its DERIVED spans are the calendar months.  The
    # created list runs chronologically from January of ``first_year``, so
    # today's month sits at index ``12 + (today.month - 1)``.
    anchor_period = created[12 + (today.month - 1)]
    anchor_span = calendar_for(user.id).period_by_id(anchor_period.id)
    assert anchor_span is not None and anchor_span.covers(today), (
        f"anchor_period {anchor_span} does not contain today={today}; "
        f"fixture invariant broken"
    )
    return all_periods, anchor_period


def _neutralize_seed_checking(db, seed_user, anchor_period):
    """Re-anchor the ``seed_user`` Checking account to $0 at *anchor_period*.

    The per-kind cross-page fixtures isolate a single non-cash account so
    the AGGREGATE surfaces (year-end net worth, the savings net-worth
    trend) reflect ONLY that account.  ``seed_user`` always provisions one
    Checking account at a $1,000 anchor, so this neutralises it: it appends
    a $0 ``AccountAnchorHistory`` row dated on *anchor_period*'s first day, and
    that day is later than the origination assertion's, so ``resolve_anchor``
    -- which orders on ``(observed_on, created_at, id)`` -- returns $0.  A $0
    asset anchored at the current period contributes 0 to every net-worth sum
    from the current period forward and gates the trend at the current
    period, so the per-kind account's value stands alone.

    The account is re-fetched by primary key because the period-build
    commit expires every object loaded upstream in ``seed_user``; setting
    attributes on an expired instance does not reliably re-mark the row
    dirty in every SQLAlchemy ORM mode (the same refetch
    ``seed_cross_page_account`` performs for its own anchor override).

    Args:
        db: The SQLAlchemy ``db`` fixture.
        seed_user: The ``seed_user`` fixture dict.
        anchor_period: The period to re-anchor the Checking account to (the
            current / anchor period of the per-kind fixture).
    """
    # Pylint: ``import-outside-toplevel`` -- Account is intentionally not
    # imported at conftest top (every fixture builds accounts via the
    # canonical factory); load it lazily, as seed_cross_page_account does.
    # pylint: disable=import-outside-toplevel
    from app.models.account import Account
    from tests._test_helpers import override_anchor

    account = db.session.get(Account, seed_user["account"].id)
    open_books_before_the_first_assertion(db.session, account)
    override_anchor(
        db.session, account, anchor_period, Decimal("0.00"),
    )


@pytest.fixture()
def seed_cross_page_account(app, db, seed_user):
    """Factory fixture for the PT-01 cross-page balance equality lock (HIGH-01).

    Returns a callable ``build(anchor_balance, expense_amount, entries)``
    that materialises one symptom-tuple case shared across every
    balance-rendering surface (grid, /savings, /accounts checking detail,
    dashboard, year-end net-worth per-account, calendar).  The fixture
    realises the structural premise of HIGH-01 -- the developer's worst
    two symptoms (#1 $160 grid vs $114.29 /savings; #5 /accounts matches
    nowhere) had zero falsifying tests until this lock landed (Commit 11).

    The realised data shape is invariant across cases:

      * The user's existing seed_user bootstrap pay period is removed and
        replaced with 24 calendar-monthly periods spanning
        ``[today.year - 1, today.year + 1]``.  Monthly (not biweekly)
        periods are chosen deliberately so the anchor period's
        ``end_date`` IS a calendar month-end -- the C9-3 boundary
        invariant of the seam's :func:`app.services.balance_at.cash_balance_at`
        then guarantees the calendar surface's projected month-end balance
        equals the anchor-period balance for the same data.
        Without that alignment a mid-period month-end would silently make
        the calendar surface look like a divergence even when the
        underlying math agrees, defeating the cross-page lock.
      * The anchor period is the calendar month containing today (so the
        dashboard's containment lookup and the grid's window both naturally
        land on the anchor period without any date-mock plumbing).  Those
        two named ``get_current_period`` and ``get_periods_in_range`` until
        pay-calendar plan steps C2-f2b and C2-f3a deleted them; the
        alignment is what matters and it is unchanged.
      * The account anchor is overridden -- via a fresh
        ``AccountAnchorHistory`` row + cache-column update, latest-wins
        per E-19 -- to the case's ``anchor_balance``.  ``seed_user``'s
        factory-default $1,000 anchor is irrelevant here.
      * A single Projected envelope expense in the anchor period, the
        engine's own row of a definition whose stated price is
        ``expense_amount`` (plan step balance:X-cf), and the supplied entries
        list, each entry dated ``anchor_period.start_date`` (so all
        entries fall on or before any month-end ``as_of`` the calendar
        surface evaluates).

    The factory returns a context dict keyed:

      * ``user_id``, ``account``, ``account_id``, ``scenario``,
        ``scenario_id``: identifiers callers pass to every surface.
      * ``all_periods``, ``anchor_period``: the period list and the
        chosen anchor period.
      * ``year``, ``month``: ``anchor_period.start_date.year /
        .month`` -- the calendar/year-end surfaces consume these.

    The five remediation-plan cases (PT-01 base, zero anchor, negative
    overdraft, credit-only entries, uncleared-floor) are realised by the
    test's ``@pytest.mark.parametrize`` block, not baked into the
    fixture, so a future case can be added in one place without growing
    a conftest variant.

    Returns:
        Callable ``(anchor_balance, expense_amount, entries) ->
        dict``.  Each entry is a 3-tuple ``(amount, is_credit,
        is_settled)`` -- amount as ``Decimal`` (from string), and the two
        booleans as the entries-aware reduction's discriminants.
        ``is_settled`` stamps the purchase's ``settled_on`` with the anchor
        period's start day, which is the assertion's own ``observed_on``
        here, so the purchase reads as already inside the asserted balance
        (ruling R-DH (d)); ``False`` leaves it NULL and outstanding.
    """
    # pylint: disable=import-outside-toplevel
    from app.models.account import Account
    from app.models.transaction_entry import TransactionEntry
    from tests._test_helpers import override_anchor

    def _build(
        anchor_balance: Decimal,
        expense_amount: Decimal,
        entries: list[tuple[Decimal, bool, bool]],
    ) -> dict:
        user = seed_user["user"]
        account = seed_user["account"]
        scenario = seed_user["scenario"]

        # Build the calendar-monthly period grid and locate the anchor
        # month containing today via the shared period builder (which also
        # documents why the seed_user bootstrap is deliberately kept).
        all_periods, anchor_period = _build_cross_page_calendar_periods(
            db, user,
        )

        # Override the anchor balance and matching history row.  The
        # ``seed_user`` factory already wrote an origination history
        # row at $1,000 against the bootstrap period; appending a
        # newer row makes ``resolve_anchor`` (latest-wins by
        # ``created_at``) return the case's balance instead.  The
        # cache columns are updated in the same flush so the
        # resolver's cache-reconciliation path stays quiet (cache ==
        # latest event), which keeps the test log free of spurious
        # ``EVT_ANCHOR_CACHE_RECONCILED`` entries.
        #
        # Re-fetch ``account`` against the live session because the
        # earlier ``db.session.commit()`` (after the new period
        # inserts) expires every object loaded in this session
        # (``expires_on_commit=True``).  Setting attributes on an
        # expired instance whose load fixture lives upstream in
        # ``seed_user`` does not reliably re-mark the row dirty in
        # every SQLAlchemy ORM mode -- the symptom is a silent
        # ``EVT_ANCHOR_CACHE_RECONCILED`` log entry on every surface
        # read because the cache column did not actually move.
        # Refetching by primary key gives us a known-attached
        # instance whose attribute assignments are guaranteed to
        # mark the row dirty for the next flush.
        account = db.session.get(Account, seed_user["account"].id)
        open_books_before_the_first_assertion(db.session, account)
        override_anchor(
            db.session, account, anchor_period, anchor_balance,
        )

        # Single Projected envelope expense in the anchor period, generated by
        # the engine from a definition that states its price and repeats
        # every paycheck (plan step balance:X-cf).  ``is_envelope=True`` on
        # the template is what makes the entries-aware reduction applicable
        # -- a generated row defers to its template's flag -- while a
        # non-envelope one would short-circuit to ``effective_amount``
        # regardless of entries.
        template = make_expense_template(
            db.session, seed_user, amount=str(expense_amount),
            name="PT-01 envelope expense", category_key="Groceries",
            is_envelope=True,
        )
        txn = generate_row_of(template, anchor_period)

        # Entries all dated on anchor_period.start_date -- a date that
        # is on or before every month-end ``as_of`` the calendar
        # surface evaluates, so the E-27 entry-date cut is a no-op
        # for this fixture and the calendar surface's balance equals
        # the resolver's anchor-period balance by construction.
        for amount, is_credit, is_settled in entries:
            db.session.add(TransactionEntry(
                **figure_source_columns(),
                transaction_id=txn.id, account_id=txn.account_id, owner_id=txn.user_id,
                user_id=user.id,
                amount=amount,
                description="PT-01 entry",
                purchased_on=anchor_period.start_date,
                # The assertion ``override_anchor`` wrote is observed on this
                # same day, so a purchase settled on it is INSIDE that balance
                # -- the state the retired ``settled_on=anchor_period.start_date`` flag named.
                **settle_day_columns(
                    anchor_period.start_date if is_settled else None
                ),
                is_credit=is_credit,
            ))
        db.session.commit()

        return {
            "user_id": user.id,
            "account": account,
            "account_id": account.id,
            "scenario": scenario,
            "scenario_id": scenario.id,
            "all_periods": all_periods,
            "anchor_period": anchor_period,
            "year": anchor_period.start_date.year,
            "month": anchor_period.start_date.month,
        }

    return _build


@pytest.fixture()
def cross_page_loan_ctx(db, seed_user):
    """Single isolated amortizing loan for the cross-page equality lock.

    Builds the shared calendar-monthly period grid, neutralises the
    seed_user Checking account to $0 (so the AGGREGATE surfaces -- year-end
    net worth, the savings net-worth trend -- reflect the loan alone), then
    creates ONE amortizing loan whose original principal P differs from its
    trued-up current balance C: ``create_loan_account`` sets P = $240,000 at
    origination, and an ``insert_trueup_event`` dated today asserts C =
    $200,000.  The trueup makes the resolver schedule today-forward (first
    payment next month), so every period up to and including the anchor
    reports C held flat -- NEVER P.  ``C != P`` is what makes the boundary
    assertion non-tautological (returning P at a pre-payment period is the
    exact historical bug PR #44 / aba0242 fixed).

    Returns a ctx dict mirroring ``seed_cross_page_account`` plus ``C``,
    ``P``, and ``pre_anchor_period`` (a period strictly before the anchor,
    where the balance map must equal C and not P).
    """
    # Pylint: ``import-outside-toplevel`` -- LoanParams is loaded lazily,
    # the Account-class convention this conftest follows (no model packages
    # imported at module top).
    # pylint: disable=import-outside-toplevel
    from app.models.loan_params import LoanParams

    user = seed_user["user"]
    scenario = seed_user["scenario"]
    all_periods, anchor_period = _build_cross_page_calendar_periods(db, user)
    _neutralize_seed_checking(db, seed_user, anchor_period)

    today = date.today()
    original_principal = Decimal("240000.00")  # P
    current_balance = Decimal("200000.00")     # C (trued up today); C != P
    loan = create_loan_account(
        seed_user, db.session, name="Cross-Page Loan",
        principal=original_principal, term=360,
        origination_date=date(today.year - 1, 1, 1),
    )
    params = db.session.query(LoanParams).filter_by(account_id=loan.id).one()
    insert_trueup_event(params, current_balance, anchor_date=today)
    db.session.commit()

    # Pre-anchor period: the period immediately before the anchor (current)
    # period.  The trueup is dated today and the loan's first scheduled
    # payment is next month, so this period reports C held flat -- the
    # boundary the bug used to fill with the original principal P.
    anchor_pos = next(
        i for i, p in enumerate(all_periods) if p.id == anchor_period.id
    )
    pre_anchor_period = all_periods[anchor_pos - 1]

    return {
        "user_id": user.id,
        "account": loan,
        "account_id": loan.id,
        "scenario": scenario,
        "scenario_id": scenario.id,
        "all_periods": all_periods,
        "anchor_period": anchor_period,
        "year": anchor_period.start_date.year,
        "month": anchor_period.start_date.month,
        "C": current_balance,
        "P": original_principal,
        "pre_anchor_period": pre_anchor_period,
    }


@pytest.fixture()
def cross_page_loan_unpaid_ctx(db, seed_user):
    """An amortizing loan originated in the PAST with NO payment and NO true-up.

    The loan shape the cross-page lock could not previously see: the one in which
    a schedule-walking producer CAN phantom-pay the debt down.
    ``cross_page_loan_ctx`` asserts a true-up dated TODAY, which re-anchors the
    resolver's schedule today-forward and so leaves no past-dated UNPAID rows
    behind; this fixture deliberately leaves them.  Originated 18 months ago at
    $240,000 over 360 months, never paid and never trued up, the resolver's
    schedule carries ~17 PROJECTED installments dated on or before today.

    Every one of those rows is a payment that was never made, so not one dollar of
    principal was ever paid: the honest balance is the full $240,000 opening, on
    EVERY surface and at EVERY period.  A producer that walked the schedule instead
    of the ledger would report LESS.

    The loan IS opened in the ledger (``create_loan_account`` writes through
    production's reconcile path), exactly as a real configured loan is -- so on
    the current code every surface reads the ledger and agrees at $240,000, and
    the accompanying test PASSES.  It is a regression lock, not a reproducer: it
    holds the line against a schedule producer being reintroduced for the past,
    which is what let the /savings tile and the net-worth trend disagree on their
    own 'today' point (a no-ledger path production never took, but every loan test
    did).

    Returns a ctx dict mirroring ``cross_page_loan_ctx``, plus ``P`` (the original
    principal, which is also the correct balance everywhere) and
    ``past_period`` (a period strictly before today, where an unpaid projected
    installment would have phantom-paid the debt down).
    """
    user = seed_user["user"]
    scenario = seed_user["scenario"]
    all_periods, anchor_period = _build_cross_page_calendar_periods(db, user)
    _neutralize_seed_checking(db, seed_user, anchor_period)

    today = date.today()
    original_principal = Decimal("240000.00")  # P -- never paid down
    loan = create_loan_account(
        seed_user, db.session, name="Never-Paid Loan",
        principal=original_principal, rate=Decimal("0.06000"), term=360,
        origination_date=today - timedelta(days=548),
    )
    db.session.commit()

    anchor_pos = next(
        i for i, p in enumerate(all_periods) if p.id == anchor_period.id
    )
    past_period = all_periods[anchor_pos - 1]

    return {
        "user_id": user.id,
        "account": loan,
        "account_id": loan.id,
        "scenario": scenario,
        "scenario_id": scenario.id,
        "all_periods": all_periods,
        "anchor_period": anchor_period,
        "year": anchor_period.start_date.year,
        "month": anchor_period.start_date.month,
        "P": original_principal,
        "past_period": past_period,
    }


def _unseeded_replay_balance(loan_id, scenario_id, as_of):
    """Return a loan's un-seeded schedule-replay balance (the pre-switch value).

    :func:`tests._test_helpers.unseeded_replay_balance`, the suite's ONE
    assembly of that replay (plan step **balance:X-bl-2b**).  Kept as a name
    here because the cross-page fixtures below read like prose with it.
    """
    # Pylint: ``import-outside-toplevel`` -- the app services are loaded lazily,
    # the convention every helper in this conftest follows.
    # pylint: disable=import-outside-toplevel
    from tests._test_helpers import unseeded_replay_balance

    return unseeded_replay_balance(loan_id, scenario_id, as_of)


@pytest.fixture()
def cross_page_loan_off_schedule_ctx(db, seed_user):
    """A GENESIS loan with an OFF-SCHEDULE confirmed payment (the read switch).

    Unlike ``cross_page_loan_ctx`` -- which posts no genesis and so resolves via
    the anchor-replay fallback -- this loan is OPENED in the ledger (a settled
    payment fires the genesis sync: opening + true-up corrections + the payment
    split) and its one confirmed payment pays cash far above the scheduled P&I,
    so the REAL principal it books down diverges from the schedule replay.  The
    read switch (plan Section 8) makes the SCALAR surfaces -- the /savings tile
    (``_compute_loan_account``) and the loan-detail page (the ``balance_at``
    seam scalar since plan C4) -- read that real ledger balance, NOT the replay.

    Returns the reader-shaped ctx plus ``ledger`` (the genesis reader's balance,
    what the surfaces must now show) and ``replay`` (the un-seeded resolver's
    schedule balance, what they showed BEFORE the switch); the test asserts the
    two DIVERGE so the "surfaces == ledger" equality is non-vacuous.  Since the
    Section 9 per-period read switch, the year-end and net-worth-trend MAP
    surfaces ALSO read the confirmed ledger off-schedule, so the whole
    four-surface set agrees on the real balance (see
    ``test_all_surfaces_read_the_ledger_off_schedule``).
    """
    # Pylint: ``import-outside-toplevel`` -- the app services / test helpers are
    # loaded lazily, the convention every fixture in this conftest follows.
    # pylint: disable=import-outside-toplevel
    from tests._test_helpers import (
        create_loan_with_trueup,
        create_settled_transfer,
    )

    user = seed_user["user"]
    scenario = seed_user["scenario"]
    today = date.today()
    all_periods, anchor_period = _build_cross_page_calendar_periods(db, user)
    _neutralize_seed_checking(db, seed_user, anchor_period)

    # One OFF-SCHEDULE payment ($5,000 cash vs ~$1,499 scheduled P&I) two months
    # before today, so its pay period has begun and the ledger books the extra
    # ~$3,500 of real principal the schedule replay drops on the floor.
    anchor_idx = next(
        i for i, p in enumerate(all_periods) if p.id == anchor_period.id
    )
    payment_period = all_periods[anchor_idx - 2]

    # Origination two years back at $250k, trued up to $200k the day before the
    # payment's pay period begins, 6% fixed.  origination != anchor so the walk
    # seeds from the true-up, and the loan carries the opening + true-up
    # genesis corrections.  The true-up sits after every installment before the
    # payment's own (the first due day on or after its period start): every
    # contractual installment is charged since plan step recurrence:R16-c-2,
    # so the year-back true-up it carried until then left a year of months
    # standing for the one payment to clear (ruling R-R103).
    loan = create_loan_with_trueup(
        seed_user, db.session,
        origination_principal=Decimal("250000.00"),
        anchor_balance=Decimal("200000.00"),
        anchor_date=payment_period.start_date - timedelta(days=1),
        rate=Decimal("0.06000"),
        origination_date=date(today.year - 2, 1, 1),
        name="Off-Schedule Loan",
    )
    # Settled on its own period start (a past date), so it is visible today under
    # C2's settled-date clock regardless of the UTC/display-tz offset.
    create_settled_transfer(
        seed_user, db.session, seed_user["account"], loan, payment_period,
        amount=Decimal("5000.00"),
        settled_on=payment_period.start_date,
    )
    db.session.commit()

    # The genesis reader (what the surfaces now read) vs the un-seeded schedule
    # replay (what they read before the switch): off-schedule they DIVERGE.
    ledger = posted_loan_balance_at(
        loan.id, scenario.id, today,
    )
    replay = _unseeded_replay_balance(loan.id, scenario.id, today)

    return {
        "user_id": user.id,
        "account": loan,
        "account_id": loan.id,
        "scenario": scenario,
        "scenario_id": scenario.id,
        "all_periods": all_periods,
        "anchor_period": anchor_period,
        "year": anchor_period.start_date.year,
        "month": anchor_period.start_date.month,
        "ledger": ledger,
        "replay": replay,
    }


@pytest.fixture()
def cross_page_property_ctx(db, seed_user):
    """Single isolated appreciating Property, anchor == current period.

    Builds the shared period grid, neutralises the seed_user Checking to
    $0, then creates ONE Property whose user-set market value is V =
    $400,000, anchored at the current period.

    **Anchor == current no longer means the surfaces read V** (plan step X-g2b,
    ruling R-Y): the anchor period earns its own days now, where the shipped
    producer split on ``period_index > anchor_idx`` and served that period from
    a flat carry.  What the surfaces must still agree on is the VALUE, whatever
    it is -- which is what the cross-page classes assert, and what makes them a
    wiring lock rather than a figure pin.  Returns a ctx dict mirroring
    ``seed_cross_page_account`` plus ``V``, the ASSERTED value the modelled one
    is measured against.
    """
    user = seed_user["user"]
    scenario = seed_user["scenario"]
    all_periods, anchor_period = _build_cross_page_calendar_periods(db, user)
    _neutralize_seed_checking(db, seed_user, anchor_period)

    value = Decimal("400000.00")  # V
    prop = make_appreciating_account(
        seed_user, db.session, anchor_period, value, Decimal("0.03000"),
    )

    return {
        "user_id": user.id,
        "account": prop,
        "account_id": prop.id,
        "scenario": scenario,
        "scenario_id": scenario.id,
        "all_periods": all_periods,
        "anchor_period": anchor_period,
        "year": anchor_period.start_date.year,
        "month": anchor_period.start_date.month,
        "V": value,
    }


@pytest.fixture()
def cross_page_investment_ctx(db, seed_user):
    """Single isolated Investment, anchor == current period, no contribution.

    Builds the shared period grid, neutralises the seed_user Checking to
    $0, then creates ONE 401(k) whose balance is V = $100,000, anchored at
    the current period, with no employer match and no contribution feed.
    Anchor == current used to mean every surface read the anchor balance V,
    because the growth projection skipped the anchor period entirely.  **Ruling
    R-Y gives that period its own days** (plan step X-g2b), so the surfaces read
    V plus that growth; what they must still do is agree, which is what the
    consuming test asserts -- reading the expected figure from the seam and
    pinning it strictly above V.  Returns a ctx dict mirroring
    ``seed_cross_page_account`` plus ``V``, the ASSERTED balance.
    """
    user = seed_user["user"]
    scenario = seed_user["scenario"]
    all_periods, anchor_period = _build_cross_page_calendar_periods(db, user)
    _neutralize_seed_checking(db, seed_user, anchor_period)

    value = Decimal("100000.00")  # V
    inv = make_investment_account(seed_user, db.session, anchor_period, value)

    return {
        "user_id": user.id,
        "account": inv,
        "account_id": inv.id,
        "scenario": scenario,
        "scenario_id": scenario.id,
        "all_periods": all_periods,
        "anchor_period": anchor_period,
        "year": anchor_period.start_date.year,
        "month": anchor_period.start_date.month,
        "V": value,
    }


@pytest.fixture()
def cross_page_investment_past_anchor_ctx(db, seed_user):
    """Single isolated Investment anchored 6 monthly periods IN THE PAST.

    The model-from-anchor divergence fixture the Level 1 savings-tile reroute
    (the :mod:`app.services.balance_at` seam) unlocks.  A 401(k) with opening
    balance ``V0`` = $100,000 and a 7% assumed return
    (``make_investment_account``), anchored 6 monthly periods BEFORE today's
    period, with no contribution feed -- so the only post-anchor movement is
    compounding.  The cash-basis carry holds ``V0`` flat to today; the
    kernel's model-from-anchor map compounds ``V0`` forward to today, so the
    modeled balance at the current period is STRICTLY GREATER than ``V0``.
    That gap is what makes the cross-producer lock non-tautological: before
    the reroute the /savings tile read the flat ``V0`` while the year-end and
    net-worth-trend surfaces read the modeled value, and the reroute makes the
    tile adopt the modeled value the other kernel surfaces already report.

    Contrast ``cross_page_investment_ctx`` (anchor == current), where the
    divergence is only that period's own growth (ruling R-Y) rather than a whole
    anchor-to-today span.

    The seed_user Checking is neutralised to $0 so the AGGREGATE surfaces
    (year-end net worth, the savings net-worth trend) reflect the investment
    alone.  Returns a ctx dict mirroring the other per-kind fixtures plus
    ``V0`` (the flat cash-basis carry) and ``current_period`` (today's period,
    where every surface is read); ``anchor_period`` here is today's period
    (the dashboard's current period), NOT the account's past anchor.
    """
    user = seed_user["user"]
    scenario = seed_user["scenario"]
    all_periods, anchor_period = _build_cross_page_calendar_periods(db, user)
    _neutralize_seed_checking(db, seed_user, anchor_period)

    # Anchor the investment 6 monthly periods before today's period so the
    # model-from-anchor map compounds it forward across that gap.  The grid
    # puts today's period well above index 6 (bootstrap at 0, then 12 prior
    # monthly periods before today's month), so ``anchor_pos - 6`` is always a
    # real past period.
    anchor_pos = next(
        i for i, p in enumerate(all_periods) if p.id == anchor_period.id
    )
    past_anchor_period = all_periods[anchor_pos - 6]

    opening_balance = Decimal("100000.00")  # V0 -- the flat cash-basis carry
    inv = make_investment_account(
        seed_user, db.session, past_anchor_period, opening_balance,
    )

    return {
        "user_id": user.id,
        "account": inv,
        "account_id": inv.id,
        "scenario": scenario,
        "scenario_id": scenario.id,
        "all_periods": all_periods,
        "anchor_period": anchor_period,
        "current_period": anchor_period,
        "year": anchor_period.start_date.year,
        "month": anchor_period.start_date.month,
        "V0": opening_balance,
    }


@pytest.fixture()
def cross_page_secured_ctx(db, seed_user):
    """Property (PV) secured by a mortgage (MC) for the home-equity relationship.

    Builds the shared period grid, neutralises the seed_user Checking to
    $0, then creates ONE Property (market value PV = $400,000, anchored at
    the current period) and ONE mortgage trued up to current balance MC =
    $250,000 today, linked via ``mortgage.collateral_account_id =
    property.id`` so the property's ``secured_loans`` backref lists the
    mortgage.  The relationship under test: market value (PV) minus total
    secured debt (MC) is the equity, on the equity producer's own basis.

    **Since plan step X-g2b the /savings tile reads the MODELLED property value
    while that producer still reads the ``current_anchor_balance`` cache
    column** (finding N-83, scheduled for its own commit), so the consuming test
    asserts the two differ by exactly that gap rather than matching.  Returns
    PV, MC, and both account handles plus the standard period keys.
    """
    # Pylint: ``import-outside-toplevel`` -- Account / LoanParams are loaded
    # lazily, the Account-class convention this conftest follows (no model
    # packages imported at module top).
    # pylint: disable=import-outside-toplevel
    from app.models.account import Account
    from app.models.loan_params import LoanParams

    user = seed_user["user"]
    scenario = seed_user["scenario"]
    all_periods, anchor_period = _build_cross_page_calendar_periods(db, user)
    _neutralize_seed_checking(db, seed_user, anchor_period)

    today = date.today()
    property_value = Decimal("400000.00")    # PV
    mortgage_balance = Decimal("250000.00")  # MC
    prop = make_appreciating_account(
        seed_user, db.session, anchor_period, property_value,
        Decimal("0.03000"),
    )
    mortgage = create_loan_account(
        seed_user, db.session, name="Cross-Page Mortgage",
        principal=Decimal("300000.00"), term=360,
        origination_date=date(today.year - 1, 1, 1),
    )
    params = db.session.query(LoanParams).filter_by(
        account_id=mortgage.id,
    ).one()
    insert_trueup_event(params, mortgage_balance, anchor_date=today)
    # Re-fetch the mortgage by primary key so setting the collateral FK
    # reliably marks the row dirty (the create_loan_account commit expired
    # it, the same refetch reason as _neutralize_seed_checking).
    mortgage = db.session.get(Account, mortgage.id)
    mortgage.collateral_account_id = prop.id
    db.session.commit()

    return {
        "user_id": user.id,
        "property_account": prop,
        "property_account_id": prop.id,
        "mortgage_account": mortgage,
        "mortgage_account_id": mortgage.id,
        "scenario": scenario,
        "scenario_id": scenario.id,
        "all_periods": all_periods,
        "anchor_period": anchor_period,
        "year": anchor_period.start_date.year,
        "month": anchor_period.start_date.month,
        "PV": property_value,
        "MC": mortgage_balance,
    }


@pytest.fixture()
def second_user(app, db):
    """Create a second user for IDOR and cross-user isolation testing.

    Mirrors the shape of seed_user so the two can be used interchangeably.

    Returns:
        dict with keys: user, settings, account, scenario, categories.
    """
    user = User(
        email="other@shekel.local",
        password_hash=hash_password("otherpass"),
        display_name="Other User",
    )
    db.session.add(user)
    db.session.flush()

    settings = UserSettings(user_id=user.id)
    db.session.add(settings)

    # The owner's opening pay period and the schedule row beside it, from the
    # writer that owns both; see ``_test_helpers.open_owner_calendar`` for
    # the argument.
    bootstrap_period = open_owner_calendar(
        user.id, SEED_USER_BOOTSTRAP_START, cadence_days=SEED_USER_CADENCE_DAYS,
    )[0]

    checking_type = (
        db.session.query(AccountType).filter_by(name="Checking").one()
    )
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=checking_type.id,
            name="Other Checking",
            anchor_balance=Decimal("500.00"),
            # Day one of its own period -- see ``seed_user`` above for why
            # the origination must not share a civil day with the settles.
            observed_on=bootstrap_period.start_date,
        ),
    )
    # The BOOKS open the day before that assertion -- see ``seed_user`` above
    # for the argument (plan step X-f3c-2b, ruling **R-HG**).
    open_books_before_the_first_assertion(db.session, account)

    scenario = Scenario(
        user_id=user.id,
        name="Baseline",
        is_baseline=True,
    )
    db.session.add(scenario)
    db.session.flush()

    categories = []
    for group, item in [
        ("Income", "Salary"),
        ("Home", "Rent"),
    ]:
        cat = Category(
            user_id=user.id,
            group_name=group,
            item_name=item,
        )
        db.session.add(cat)
        categories.append(cat)
    db.session.flush()

    db.session.commit()

    return {
        "user": user,
        "settings": settings,
        "account": account,
        "scenario": scenario,
        "categories": {c.item_name: c for c in categories},
        "bootstrap_period": bootstrap_period,
    }


@pytest.fixture()
def seed_periods_52(app, db, seed_user):
    """Rebuild the seeded owner's schedule as 52 pay periods from 2026-01-02.

    :func:`seed_periods` at a two-year horizon, for the FIN cases that need
    production-scale data volumes.  Same door, same derivation.

    Returns:
        List of PayPeriod objects, payday ascending.
    """
    return _reset_seed_calendar(
        seed_user, date(2026, 1, 2), 52, SEED_USER_CADENCE_DAYS,
    )


# --- Two-User Isolation Fixtures ------------------------------------------


@pytest.fixture()
def seed_second_user(app, db):
    """Create an independent second user for multi-user isolation testing.

    Mirrors seed_user in structure but creates entirely separate objects
    with distinguishable names and amounts.

    Returns:
        dict with keys: user, settings, account, scenario, categories.
    """
    user = User(
        email="second@shekel.local",
        password_hash=hash_password("secondpass12"),
        display_name="Second User",
    )
    db.session.add(user)
    db.session.flush()

    settings = UserSettings(user_id=user.id)
    db.session.add(settings)

    # The owner's opening pay period and the schedule row beside it, from the
    # writer that owns both; see ``_test_helpers.open_owner_calendar`` for
    # the argument.
    bootstrap_period = open_owner_calendar(
        user.id, SEED_USER_BOOTSTRAP_START, cadence_days=SEED_USER_CADENCE_DAYS,
    )[0]

    # Baseline scenario BEFORE the account, mirroring ``seed_user`` (and
    # production registration): ``create_account`` posts the opening anchor
    # correction into every scenario, so the second Checking carries its
    # $2000.00 opening from t0.
    scenario = Scenario(
        user_id=user.id,
        name="Baseline",
        is_baseline=True,
    )
    db.session.add(scenario)
    db.session.flush()

    checking_type = (
        db.session.query(AccountType).filter_by(name="Checking").one()
    )
    account = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=checking_type.id,
            name="Checking",
            anchor_balance=Decimal("2000.00"),
            # Day one of its own period -- see ``seed_user`` above for why
            # the origination must not share a civil day with the settles.
            observed_on=bootstrap_period.start_date,
        ),
    )
    # The BOOKS open the day before that assertion -- see ``seed_user`` above
    # for the argument (plan step X-f3c-2b, ruling **R-HG**).
    open_books_before_the_first_assertion(db.session, account)

    categories = []
    for group, item in [
        ("Income", "Salary"),
        ("Home", "Rent"),
        ("Auto", "Car Payment"),
        ("Family", "Groceries"),
        ("Credit Card", "Payback"),
    ]:
        cat = Category(
            user_id=user.id,
            group_name=group,
            item_name=item,
        )
        db.session.add(cat)
        categories.append(cat)
    db.session.flush()

    db.session.commit()

    return {
        "user": user,
        "settings": settings,
        "account": account,
        "scenario": scenario,
        "categories": {c.item_name: c for c in categories},
        "bootstrap_period": bootstrap_period,
    }


@pytest.fixture()
def seed_second_periods(app, db, seed_second_user):
    """Rebuild the SECOND owner's schedule as 10 pay periods from 2026-01-02.

    :func:`seed_periods` for the isolation fixtures' other owner, through the
    same door and against the same dates, so a cross-user case compares two
    calendars built the one way.

    Returns:
        List of PayPeriod objects, payday ascending.
    """
    return _reset_seed_calendar(
        seed_second_user, date(2026, 1, 2), 10, SEED_USER_CADENCE_DAYS,
    )


@pytest.fixture()
def second_auth_client(app, db, seed_second_user):
    """Provide an authenticated test client for the second user.

    Creates a NEW test client instance to avoid session conflicts
    with the primary auth_client.
    """
    second_client = app.test_client()
    resp = second_client.post("/login", data={
        "email": "second@shekel.local",
        "password": "secondpass12",
    })
    assert resp.status_code == 302, (
        f"second_auth_client login failed with status {resp.status_code}"
    )
    return second_client


def _build_full_user_data(db, seed_user, periods):
    """Build the rich-dataset payload shared by seed_full_user_data variants.

    Extracted so both ``seed_full_user_data`` (calendar-anchored) and
    ``seed_full_user_data_today`` (today-relative) can share a single
    body and only differ in which ``periods`` fixture they consume.

    Args:
        db:        SQLAlchemy db extension (the test ``db`` fixture).
        seed_user: dict from the ``seed_user`` fixture.
        periods:   List of PayPeriod objects from a periods fixture.

    Returns:
        dict merging seed_user keys plus: periods, template, transaction,
        savings_goal, recurrence_rule, savings_account,
        transfer_template, salary_profile.
    """
    user = seed_user["user"]
    account = seed_user["account"]
    scenario = seed_user["scenario"]

    # Look up reference data.
    savings_acct_type = (
        db.session.query(AccountType).filter_by(name="Savings").one()
    )
    filing_single = (
        db.session.query(FilingStatus).filter_by(name="single").one()
    )

    # a) Recurrence rule + transaction template + transaction.  The
    # definition states its price and its cadence, and the ENGINE writes its
    # row in the first period (plan step balance:X-cf): the row is derived,
    # dated and answers an occurrence, exactly as the app's own would be.
    template = make_expense_template(
        db.session, seed_user, amount="1200.00", name="Rent Payment",
    )
    rule = template.recurrence_rule
    txn = generate_row_of(template, periods[0])

    # b) Savings goal.
    goal = SavingsGoal(
        user_id=user.id,
        account_id=account.id,
        name="Emergency Fund",
        target_amount=Decimal("10000.00"),
    )
    db.session.add(goal)

    # c) Savings account + transfer template via the canonical
    # factory (E-19, Commit 3).
    savings_account = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=savings_acct_type.id,
            name="Savings",
            anchor_balance=Decimal("500.00"),
        ),
    )
    # **Its books open the day BEFORE its origination assertion** (plan step
    # X-f3c-2b).  The factory defaults ``observed_on`` to ``display_today()``
    # and the settle door defaults a settle day to the SAME ``display_today()``
    # -- so every fixture meaning "this account existed, then money moved on
    # it" landed the movement on the very day the books open, which ruling
    # R-HG makes unrecordable (the opening equity IS that day's closing
    # balance).  The same repair production took, and it moves no figure.
    open_books_before_the_first_assertion(db.session, savings_account)

    transfer_tpl = TransferTemplate(
        user_id=user.id,
        from_account_id=account.id,
        to_account_id=savings_account.id,
        name="Monthly Savings",
        default_amount=Decimal("200.00"),
    )
    db.session.add(transfer_tpl)
    db.session.flush()
    # A definition STATES its price, as every app-side create door does.  Since
    # plan step balance:X-au-f a generated TRANSFER stores no figure and is
    # priced by this series on its own due date, so a template without one
    # generates rows ``_stated_amount`` REFUSES.
    state_template_price(transfer_tpl)
    # ...and its CADENCE, so a test wanting a transfer of this definition
    # takes the ENGINE's (:func:`generate_transfer_of`, plan step
    # balance:X-ch) rather than hand-building one.  No transfer is generated
    # here: unlike the transaction template above, no fixture reads one.
    make_every_period_rule(db.session, transfer_tpl)

    # d) Salary profile.
    salary_profile = SalaryProfile(
        user_id=user.id,
        scenario_id=scenario.id,
        filing_status_id=filing_single.id,
        name="Day Job",
        state_code="NC",
    )
    db.session.add(salary_profile)
    # Its pay list (plan step salary:X-av-3a): $75,000.00 a year at 26 paychecks, rounded to the cent, from
    # the first period's payday -- what migration 70680a4a7405 writes for the
    # yearly figure this fixture carried before.
    start_test_pay_list(
        salary_profile, Decimal("2884.62"), periods[0].start_date,
    )

    db.session.commit()

    return {
        **seed_user,
        "periods": periods,
        "template": template,
        "transaction": txn,
        "savings_goal": goal,
        "recurrence_rule": rule,
        "savings_account": savings_account,
        "transfer_template": transfer_tpl,
        "salary_profile": salary_profile,
    }


@pytest.fixture()
def seed_full_user_data(app, db, seed_user, seed_periods):
    """Create a rich dataset for User A (the primary test user).

    Includes transaction template, transaction, savings goal, savings
    account, transfer template, and salary profile. All objects have
    distinguishable names and amounts for use in isolation testing.

    Uses the calendar-anchored ``seed_periods`` fixture, so transactions
    fall in calendar 2026.  Use ``seed_full_user_data_today`` instead
    when the test exercises a route that asks which paycheck contains today.

    Returns:
        dict merging seed_user keys plus: periods, template, transaction,
        savings_goal, recurrence_rule, savings_account,
        transfer_template, salary_profile.
    """
    return _build_full_user_data(db, seed_user, seed_periods)


@pytest.fixture()
def seed_full_user_data_today(app, db, seed_user, seed_periods_today):
    """Today-relative variant of seed_full_user_data.

    Identical payload to ``seed_full_user_data`` except the periods
    are anchored so today falls in period 4.  Use when the test
    exercises a route that internally asks which paycheck contains today
    (e.g. /dashboard).

    Returns:
        dict merging seed_user keys plus: periods, template, transaction,
        savings_goal, recurrence_rule, savings_account,
        transfer_template, salary_profile.
    """
    return _build_full_user_data(db, seed_user, seed_periods_today)


@pytest.fixture()
def seed_full_second_user_data(app, db, seed_second_user, seed_second_periods):
    """Create a rich dataset for User B (the second test user).

    Mirrors seed_full_user_data but with distinguishable names and
    amounts so isolation tests can verify data separation.

    Returns:
        dict merging seed_second_user keys plus: periods, template,
        transaction, savings_goal, recurrence_rule, savings_account,
        transfer_template, salary_profile.
    """
    user = seed_second_user["user"]
    account = seed_second_user["account"]
    scenario = seed_second_user["scenario"]
    periods = seed_second_periods

    # Look up reference data.
    savings_acct_type = (
        db.session.query(AccountType).filter_by(name="Savings").one()
    )
    filing_single = (
        db.session.query(FilingStatus).filter_by(name="single").one()
    )

    # a) Recurrence rule + transaction template + transaction, the engine's
    # own row as in ``_build_full_user_data`` (plan step balance:X-cf).
    template = make_expense_template(
        db.session, seed_second_user, amount="900.00", name="Second User Rent",
    )
    rule = template.recurrence_rule
    txn = generate_row_of(template, periods[0])

    # b) Savings goal.
    goal = SavingsGoal(
        user_id=user.id,
        account_id=account.id,
        name="Vacation Fund",
        target_amount=Decimal("5000.00"),
    )
    db.session.add(goal)

    # c) Savings account + transfer template via the canonical
    # factory (E-19, Commit 3).
    savings_account = account_service.create_account(
        account_service.AccountSpec(
            user_id=user.id,
            account_type_id=savings_acct_type.id,
            name="Savings",
            anchor_balance=Decimal("300.00"),
        ),
    )
    # **Its books open the day BEFORE its origination assertion** (plan step
    # X-f3c-2b).  The factory defaults ``observed_on`` to ``display_today()``
    # and the settle door defaults a settle day to the SAME ``display_today()``
    # -- so every fixture meaning "this account existed, then money moved on
    # it" landed the movement on the very day the books open, which ruling
    # R-HG makes unrecordable (the opening equity IS that day's closing
    # balance).  The same repair production took, and it moves no figure.
    open_books_before_the_first_assertion(db.session, savings_account)

    transfer_tpl = TransferTemplate(
        user_id=user.id,
        from_account_id=account.id,
        to_account_id=savings_account.id,
        name="Bi-Weekly Savings",
        default_amount=Decimal("150.00"),
    )
    db.session.add(transfer_tpl)
    db.session.flush()
    # Its price, stated as every app-side create door states it, and its
    # cadence (see the calendar-anchored twin above).
    state_template_price(transfer_tpl)
    make_every_period_rule(db.session, transfer_tpl)

    # d) Salary profile.
    salary_profile = SalaryProfile(
        user_id=user.id,
        scenario_id=scenario.id,
        filing_status_id=filing_single.id,
        name="Second Job",
        state_code="NC",
    )
    db.session.add(salary_profile)
    # Its pay list (plan step salary:X-av-3a): $60,000.00 a year at 26 paychecks, rounded to the cent, from
    # the first period's payday -- what migration 70680a4a7405 writes for the
    # yearly figure this fixture carried before.
    start_test_pay_list(
        salary_profile, Decimal("2307.69"), periods[0].start_date,
    )

    db.session.commit()

    return {
        **seed_second_user,
        "periods": periods,
        "template": template,
        "transaction": txn,
        "savings_goal": goal,
        "recurrence_rule": rule,
        "savings_account": savings_account,
        "transfer_template": transfer_tpl,
        "salary_profile": salary_profile,
    }


# --- Entry and Companion Fixtures -----------------------------------------


@pytest.fixture()
def seed_entry_template(app, db, seed_user, seed_periods):
    """Create a template with is_envelope=True and a transaction.

    The template is an expense-type template tied to the seed_user's checking
    account with a stated price of $500.  The engine generates its single
    projected row in the first pay period (plan step balance:X-cf), so the
    row is derived and the $500 it is worth is the template's series' answer.

    Returns:
        dict with keys: template, transaction, category, recurrence_rule.
    """
    category = seed_user["categories"]["Groceries"]
    template = make_expense_template(
        db.session, seed_user, amount="500.00",
        name="Weekly Groceries", category_key="Groceries", is_envelope=True,
    )
    rule = template.recurrence_rule
    txn = generate_row_of(template, seed_periods[0])
    db.session.commit()

    return {
        "template": template,
        "transaction": txn,
        "category": category,
        "recurrence_rule": rule,
    }


@pytest.fixture()
def seed_companion(app, db, seed_user):
    """Create a companion user linked to the seed_user owner.

    The companion has role_id set to the companion role and
    linked_owner_id pointing to the primary seed_user.

    Returns:
        dict with keys: user, settings.
    """
    from app import ref_cache  # pylint: disable=import-outside-toplevel
    from app.enums import RoleEnum  # pylint: disable=import-outside-toplevel

    companion = User(
        email="companion@shekel.local",
        password_hash=hash_password("companionpass"),
        display_name="Companion User",
        role_id=ref_cache.role_id(RoleEnum.COMPANION),
        linked_owner_id=seed_user["user"].id,
    )
    db.session.add(companion)
    db.session.flush()

    settings = UserSettings(user_id=companion.id)
    db.session.add(settings)
    db.session.commit()

    return {
        "user": companion,
        "settings": settings,
    }


@pytest.fixture()
def companion_client(app, db, seed_companion):
    """Provide an authenticated test client for the companion user.

    Creates a new test client instance and logs in as the companion
    user, following the same pattern as second_auth_client.
    """
    comp_client = app.test_client()
    resp = comp_client.post("/login", data={
        "email": "companion@shekel.local",
        "password": "companionpass",
    })
    assert resp.status_code == 302, (
        f"companion_client login failed with status {resp.status_code}"
    )
    return comp_client


# --- Helpers --------------------------------------------------------------


def _refresh_ref_cache_and_jinja_globals(app):
    """Re-init ``ref_cache`` and rewrite all ID-derived Jinja globals.

    Called from two places:

      1. ``setup_database`` at session start, once the ref tables
         have been seeded for the first time.
      2. The ``db`` fixture, after the per-test TRUNCATE has wiped
         ``ref.account_types`` (via the new C-28 / F-044 FK to
         ``auth.users``) and the seed has been re-run.  The new
         seed assigns fresh IDs from the sequence; the
         pre-existing Jinja globals would otherwise point at IDs
         that no longer exist and every template that references
         one would break.

    Delegates to ``app.jinja_globals.register_ref_id_globals`` --
    the same helper ``create_app`` uses -- so the two call sites
    cannot drift out of sync.  Follow-up plan Commit 6 (F-7)
    extracted the helper; prior to that the conftest list was
    missing eight entries (timing / calc-method / goal-mode /
    income-unit IDs) and any template referencing one would raise
    ``UndefinedError`` at test time.
    """
    # pylint: disable=import-outside-toplevel
    from app import ref_cache
    from app.jinja_globals import register_ref_id_globals

    ref_cache.init(_db.session)
    # ``init`` SELECTs every ``ref`` table and writes nothing, and the session
    # it ran on is the fixture's OUTER one, which a test body's nested
    # ``app.app_context()`` cannot reach.  Left as it is, that session sits
    # idle in a transaction holding a share lock on each ``ref`` table until
    # the test ends -- and a migration replay that drops a key onto one of
    # them from the nested context (plan step ``pay_calendar:C17-a``'s
    # downgrade, run by ``rewind_pay_schedule_rhythm`` and by
    # ``test_pay_era.py`` directly) then waits on the cluster's
    # ``lock_timeout`` and fails.  Ending the read-only transaction here
    # releases the locks and changes nothing else.
    _db.session.rollback()
    register_ref_id_globals(app)


def _profile_step_stats(values):
    """Compute summary statistics for a list of float milliseconds.

    Returns dict with keys ``avg``, ``p50``, ``p95``, ``p99``,
    ``max``.  Uses :func:`statistics.quantiles` with ``n=100`` (the
    "inclusive" method, which linearly interpolates between sample
    values) for percentiles; exact at sample sizes we expect (one
    row per test, dozens to thousands).

    Special cases:

    * Empty list -- returns all zeros so the aggregator can render a
      well-formed row even if a step contributed no samples.
    * Single sample -- ``statistics.quantiles`` rejects ``n < 2``,
      so we short-circuit to the single value for every percentile.
    """
    if not values:
        return {"avg": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
    if len(values) == 1:
        single = values[0]
        return {
            "avg": single,
            "p50": single,
            "p95": single,
            "p99": single,
            "max": single,
        }

    cuts = statistics.quantiles(values, n=100, method="inclusive")
    return {
        "avg": sum(values) / len(values),
        # quantiles(n=100) returns 99 cut points; index 49 is p50,
        # index 94 is p95, index 98 is p99.
        "p50": cuts[49],
        "p95": cuts[94],
        "p99": cuts[98],
        "max": max(values),
    }


def _profile_load_rows():
    """Read every per-worker CSV in the profile dir into memory.

    Returns ``(rows, workers)`` where ``rows`` is a list of dicts
    keyed by step name with float-millisecond values, and ``workers``
    is the sorted set of worker ids that contributed.  Skips silently
    when the directory is missing (no harness output yet) so the
    aggregator can short-circuit on the empty case.
    """
    rows = []
    workers = set()
    if not _FIXTURE_PROFILE_DIR.exists():
        return rows, workers
    for csv_path in sorted(_FIXTURE_PROFILE_DIR.glob("*.csv")):
        with csv_path.open("r", newline="", encoding="utf-8") as csv_fp:
            reader = csv.DictReader(csv_fp)
            for raw in reader:
                workers.add(raw["worker_id"])
                rows.append(
                    {step: float(raw[step]) for step in _FIXTURE_PROFILE_STEPS}
                )
    return rows, sorted(workers)


def _profile_print_summary():
    """Print the per-step summary table to stdout.

    Worker-aware: aggregates across every CSV in
    ``tests/.fixture-profile/``.  Called only on the xdist controller
    or the single-process pytest run -- the two scenarios where
    ``PYTEST_XDIST_WORKER`` is unset and the process has visibility
    into every other worker's output.

    Output shape mirrors ``test-performance-research.md`` section
    3.1 (the "Per-test phase breakdown" table): one row per fixture
    inner step, then a ``Fixture setup total`` line summarising the
    sum-of-setup-steps, then ``call`` and ``teardown`` as informational
    rows (no percent column because they are not part of fixture
    setup cost).  The ``% of fixture`` column is computed relative
    to the average fixture setup total so the percentages sum to
    100 within rounding.
    """
    rows, workers = _profile_load_rows()
    if not rows:
        print()
        print("Fixture profile summary: no rows captured "
              f"(check {_FIXTURE_PROFILE_DIR})")
        print()
        return

    setup_steps = [s for s in _FIXTURE_PROFILE_STEPS if s.startswith("setup_")]
    setup_totals = [sum(row[s] for s in setup_steps) for row in rows]
    setup_avg_total = sum(setup_totals) / len(setup_totals)

    header = ["Step", "Avg", "p50", "p95", "p99", "Max", "% of fixture"]
    widths = [34, 10, 10, 10, 10, 10, 14]
    fmt = " | ".join(f"{{:<{w}}}" for w in widths)
    fmt = "| " + fmt + " |"
    sep = "|-" + "-|-".join("-" * w for w in widths) + "-|"

    print()
    print("=" * 100)
    print(f"  Fixture profile summary -- {len(rows)} tests across "
          f"{len(workers)} worker(s): {', '.join(workers)}")
    print("=" * 100)
    print(fmt.format(*header))
    print(sep)

    for step in setup_steps:
        stats = _profile_step_stats([row[step] for row in rows])
        pct = (stats["avg"] / setup_avg_total * 100.0) if setup_avg_total else 0.0
        print(fmt.format(
            _FIXTURE_PROFILE_LABELS[step],
            f"{stats['avg']:.1f} ms",
            f"{stats['p50']:.1f}",
            f"{stats['p95']:.1f}",
            f"{stats['p99']:.1f}",
            f"{stats['max']:.1f}",
            f"{pct:.1f} %",
        ))

    setup_stats = _profile_step_stats(setup_totals)
    print(fmt.format(
        "Fixture setup total",
        f"{setup_stats['avg']:.1f} ms",
        f"{setup_stats['p50']:.1f}",
        f"{setup_stats['p95']:.1f}",
        f"{setup_stats['p99']:.1f}",
        f"{setup_stats['max']:.1f}",
        "100.0 %",
    ))
    print(sep)

    for step in ("call", "teardown"):
        stats = _profile_step_stats([row[step] for row in rows])
        print(fmt.format(
            _FIXTURE_PROFILE_LABELS[step],
            f"{stats['avg']:.1f} ms",
            f"{stats['p50']:.1f}",
            f"{stats['p95']:.1f}",
            f"{stats['p99']:.1f}",
            f"{stats['max']:.1f}",
            "--",
        ))
    print()


def pytest_collection_modifyitems(config, items):
    """Narrow the collection to one CI shard when ``SHEKEL_TEST_SHARD`` is set.

    CI splits the suite across parallel jobs (plan step ``bank_import:X-gy``);
    each exports ``SHEKEL_TEST_SHARD=<index>/<total>`` and runs only its share.
    Unset -- every local run -- this deselects nothing.  How the split is a
    partition by construction, and why it keeps every ``xdist_group`` whole,
    is :mod:`tests._shard`.
    """
    apply_shard(config, items, os.environ.get(SHARD_ENV))


def pytest_report_header():
    """Head the report with the shard when this run is ONE SHARD of the suite.

    Under xdist a shard's deselected items never reach the summary line --
    measured 2026-09-22, a shard of 2,589 read ``2589 passed`` and nothing
    else -- so the report of a run that covered a sixth of the suite reads
    like a whole-suite pass.  A variable left exported in a local shell would
    produce exactly that, green.
    """
    spec = os.environ.get(SHARD_ENV)
    if spec is None:
        return None
    index, total = parse_shard(spec)
    return (
        f"{SHARD_ENV}={spec}: this run is shard {index} of {total} "
        f"(0-based) and runs ONLY that share of the suite"
    )


def pytest_sessionfinish(session, exitstatus):  # pylint: disable=unused-argument
    """Drop the per-pytest-worker database AND emit the profile summary.

    Pytest invokes this hook at the end of every session -- including
    failed sessions -- so the per-session DB is cleaned up regardless
    of pass/fail.  No-op when the xdist master process skipped the
    bootstrap (``_BOOTSTRAP_RESULT`` is ``None``); only worker
    processes own a DB to drop.

    Why psycopg directly (not SQLAlchemy):
        Flask-SQLAlchemy 3.x scopes ``db.session`` and ``db.engine``
        to the current app context, and the
        ``pytest_sessionfinish`` hook runs AFTER the session-scoped
        ``app`` fixture has torn down -- there is no active app
        context to bind to.  Wrapping the cleanup in a fresh app
        context would require either keeping the session-scoped app
        alive via module-level state or building a new app, both
        of which add complexity for the same end state.  The per-
        test ``db`` fixture already calls ``_db.session.remove`` and
        ``_db.engine.dispose`` inside its app context after every
        test, so by the time this hook runs there are no live
        SQLAlchemy connections to release -- and the
        ``WITH (FORCE)`` clause severs any backend that did
        escape, at the protocol level.  See
        ``docs/audits/test_improvements/per-worker-database-plan.md``
        Phase 3 for the broader context.

    Survives SIGKILL imperfectly: a process killed before this hook
    runs leaves an orphan DB.  Where a NEXT session reaches the same
    cluster, its bootstrap drops it via the
    ``shekel_test_{worker_id}_*`` cleanup pass (see
    :func:`_bootstrap_worker_database`), so the orphan is at worst a
    temporary disk-space cost between runs.  Under ``./scripts/test.sh``
    since ``balance:X-br-4`` there IS no next session on this cluster --
    it is removed with the container -- and since ``bank_import:X-gy`` CI
    runs every pytest invocation through that wrapper too, so no caller in
    this repository reuses a cluster across sessions any more.

    Profile aggregation:
        When ``SHEKEL_TEST_FIXTURE_PROFILE`` is set, the harness
        printed by :func:`_profile_print_summary` reads every per-
        worker CSV under ``tests/.fixture-profile/`` and writes a
        single summary table to stdout.  Only the xdist controller
        (or, in single-process runs, the test process itself)
        prints; workers are write-only.  The aggregator runs AFTER
        the DB drop so a flaky summary path cannot leave per-
        session databases behind.

    Args:
        session (pytest.Session): pytest Session object (required
            by the hook signature; unused here -- the cleanup keys
            off the module-level ``_BOOTSTRAP_RESULT`` instead).
        exitstatus (int): Session exit code.  Unused: we drop the
            per-session DB regardless of pass / fail because it is
            throwaway.
    """
    if _BOOTSTRAP_RESULT is not None:
        db_name, admin_url = _BOOTSTRAP_RESULT
        # ``try``/``finally`` so the NEW cleanup can never strand the old one.
        # Worlds go first -- a snapshot is a database of its own, and one left
        # behind would be a start state dated from a session that has ended --
        # but a snapshot drop that raised must not take the worker database's
        # drop with it, which is the cleanup this suite has always relied on.
        try:
            _drop_seeded_snapshots()
        finally:
            admin_conn = psycopg.connect(admin_url)
            try:
                admin_conn.autocommit = True
                with admin_conn.cursor() as cur:
                    cur.execute(
                        sql.SQL(
                            "DROP DATABASE IF EXISTS {} WITH (FORCE)"
                        ).format(sql.Identifier(db_name))
                    )
            finally:
                admin_conn.close()

    # Only the xdist controller / single-process run aggregates and
    # prints.  Workers (PYTEST_XDIST_WORKER set) are write-only: they
    # already appended their per-test rows to their own CSV during
    # the run, and the controller's pytest_sessionfinish fires after
    # every worker has exited, so by the time we read here every
    # row is on disk.
    if _FIXTURE_PROFILE_ENABLED and not os.environ.get("PYTEST_XDIST_WORKER"):
        _profile_print_summary()
