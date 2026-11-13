"""The per-worker test database: its bootstrap at import, and its drop and clone.

Importing this module runs :func:`_bootstrap_worker_database`, which clones this worker's
database from ``shekel_test_template`` and points ``TEST_DATABASE_URL`` at it, so it must be
imported before any ``app`` module is: ``tests/conftest.py`` imports it right after the
profile harness, and it imports nothing from ``app``.  The ``db`` fixture
(:mod:`tests._app_fixtures`) drops and re-clones the database for every test with
:func:`_drop_worker_database` and :func:`_clone_worker_database`.
"""

import os
from urllib.parse import urlparse, urlunparse

import psycopg
from psycopg import sql


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


#: The condition both by-hand rebuild hints below name.  A template built on a
#: cluster without the role carries none of the grants the role tests read
#: (``tests/test_models/test_audit_migration.py``), so the suite fails on it.
_HOLD_THE_APP_ROLE_FIRST = (
    "which must hold the shekel_app role first, as the baked image does "
    "(scripts/build_test_db_image.py)"
)


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

    Called once when this module is imported, which
    ``tests/conftest.py`` does at its own load, BEFORE any ``app``
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
        ``python scripts/build_test_template.py``, on a cluster
        that already holds the ``shekel_app`` role.

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
            # disposed.  Workers start together, and pytest-xdist also starts a
            # replacement for each crashed worker (up to ``numprocesses * 4``)
            # mid-session, under a NEW id (``xdist/dsession.py``,
            # ``_clone_node``), so this sweep runs while its siblings are live.
            # Verified on the cluster:
            # ``'shekel_test_gw11' LIKE 'shekel_test_gw1\_%'`` is false and
            # ``'shekel_test_gw1__world_x' LIKE 'shekel_test_gw1\_%'`` is true.
            #
            # **Only this worker's own names, never another's** (plan#3639,
            # ruling plan#3660).  A third pattern once took every world
            # snapshot on the cluster, ``{prefix}_%__world_%``, to collect
            # snapshots a killed run of another ``-n`` had left.  Every run now
            # has a cluster of its own (``scripts/test.sh``, ``balance:X-br-4``),
            # so any snapshot on it belongs to THIS run, and the pattern could
            # only ever take a live one: a replacement worker's sweep dropped
            # each sibling's snapshot whose owner was between tests, and every
            # later test declaring that world failed at its clone.
            cur.execute(
                "SELECT datname FROM pg_database "
                r"WHERE datname = %s OR datname LIKE %s ESCAPE '\'",
                (db_name, f"{_like_escape(db_name)}\\_%"),
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
                    "supplied this cluster.  On a cluster you control, "
                    f"{_HOLD_THE_APP_ROLE_FIRST}: "
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
                    f"cluster you supplied, {_HOLD_THE_APP_ROLE_FIRST}: "
                    "python scripts/build_test_template.py"
                )
    finally:
        verify_conn.close()

    # Point the app's TestConfig at the per-session DB.  Must
    # precede the first ``from app import ...``, which
    # ``tests/conftest.py`` makes only after importing this module.
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


def database_exists(name):
    """Whether *name* is a database on this run's cluster right now.

    Asked through a fresh admin connection rather than the app's engine: the
    subject is the catalog, not any one test's database.

    Args:
        name: The database name to look for.

    Returns:
        ``True`` when it exists.
    """
    conn = psycopg.connect(_WORKER_ADMIN_URL)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (name,),
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


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
