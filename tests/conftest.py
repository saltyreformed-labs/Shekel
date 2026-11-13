"""
Shekel Budget App -- Test Fixtures

Registers the suite's fixtures: a configured test app, a
freshly-cloned per-test database, an authenticated client, and the
data fixtures built on them.  Each is defined in a module beside this
one and registered here by importing it by name (plan#3565): the app
and the per-test database in ``tests/_app_fixtures.py``, the seeded
owner and its calendars in ``tests/_seed_owner_fixtures.py``, the
other users in ``tests/_other_user_fixtures.py``, the rich datasets in
``tests/_dataset_fixtures.py``, and the cross-page lock's in
``tests/_cross_page_fixtures.py``.  Under them sit the per-worker
database (``tests/_worker_database.py``), the seeded start states
(``tests/_seeded_start_states.py``) and the profile harness
(``tests/_fixture_profile.py``).  The suite's three hooks stay here.

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
wrapper's non-durable cluster).  ``_clone_worker_database``
(``tests/_worker_database.py``) carries the measurements
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
#   ``TEST_DATABASE_URL`` -- ``_bootstrap_worker_database``, which
#   runs when ``tests._worker_database`` is imported below, sets it to
#   the per-session DSN.
# * Production / ``_reject_sentinel`` defends read ``SECRET_KEY``
#   with no fallback (audit finding F-016).
#
# Setting either env var after the first ``from app import ...``
# would leave the app pointed at a stale value.  So the two modules
# imported first, ``tests._fixture_profile`` and
# ``tests._worker_database``, import nothing from ``app``, and every
# fixture module after them may.

import os

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

from tests._fixture_profile import _FIXTURE_PROFILE_ENABLED, _profile_print_summary
from tests._worker_database import _BOOTSTRAP_RESULT
from tests._seeded_start_states import _drop_seeded_snapshots
from tests._shard import SHARD_ENV, apply_shard, parse_shard
# Every fixture, imported by name: pytest registers each fixture it finds in this
# module's namespace with this conftest's directory as its base, so each keeps the
# name, scope, autouse and reach it had when it was defined here.
# Pylint: ``unused-import`` -- pytest reads every name these imports bind, and this
# module reads none of them.
# pylint: disable=unused-import
from tests._app_fixtures import (
    _calendar_sweep,
    app,
    below_the_transfer_column_drop,
    client,
    db,
    disable_hibp_check,
    each_request_has_its_own_g,
    fast_bcrypt,
    owner_client,
    seeded_world,
    set_totp_key,
    setup_database,
    tax_law,
)
from tests._seed_owner_fixtures import (
    auth_client,
    bare_auth_client,
    bare_periods,
    bare_user,
    bare_user_with_cadence,
    seed_periods,
    seed_periods_52,
    seed_periods_today,
    seed_schedule_at_cadence,
    seed_user,
)
from tests._cross_page_fixtures import (
    cross_page_investment_ctx,
    cross_page_investment_past_anchor_ctx,
    cross_page_loan_ctx,
    cross_page_loan_off_schedule_ctx,
    cross_page_loan_unpaid_ctx,
    cross_page_property_ctx,
    cross_page_secured_ctx,
    seed_cross_page_account,
)
from tests._other_user_fixtures import (
    companion_client,
    second_auth_client,
    second_user,
    seed_companion,
    seed_second_periods,
    seed_second_user,
)
from tests._dataset_fixtures import (
    seed_entry_template,
    seed_full_second_user_data,
    seed_full_user_data,
    seed_full_user_data_today,
)
# pylint: enable=unused-import


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
