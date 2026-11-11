"""Named seeded start states: the registry, the build into a snapshot, and the lookups.

A test declares a world with ``@pytest.mark.seeded_start_state("name")``, and the ``db``
fixture (:mod:`tests._app_fixtures`) clones its database from that world's snapshot.  The
module also holds :func:`_refresh_ref_cache_and_jinja_globals`, which a build, the ``db``
fixture and ``setup_database`` each call: this is the lowest module all three reach.
"""

import os
from datetime import date, datetime
from decimal import Decimal

import psycopg
from psycopg import sql

from app.extensions import db as _db
from tests._worker_database import (
    _SNAPSHOT_INFIX,
    _TEST_DATABASE_PREFIX,
    _TEST_TEMPLATE_DATABASE,
    _WORKER_ADMIN_URL,
    _WORKER_DB_NAME,
    _clone_worker_database,
    _drop_worker_database,
)


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
