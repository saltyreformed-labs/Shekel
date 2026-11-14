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
(``tests/_fixture_profile.py``).  The suite's four hooks stay here.

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

# Pylint: wrong-import-position, wrong-import-order -- SECRET_KEY and the
# two-factor key must be set, and the per-worker database cloned, before any
# ``app`` module is imported; the comment below says why.
# pylint: disable=wrong-import-position,wrong-import-order
# Imports below are intentionally ordered so the SECRET_KEY and
# TOTP_ENCRYPTION_KEY env vars are set AND the per-pytest-worker
# database is cloned from ``shekel_test_template`` BEFORE any ``app``
# module is imported.  Three class-body reads at first-app-import time
# depend on this:
#
# * ``app.config.TestConfig.SQLALCHEMY_DATABASE_URI`` reads
#   ``TEST_DATABASE_URL`` -- ``_bootstrap_worker_database``, which
#   runs when ``tests._worker_database`` is imported below, sets it to
#   the per-session DSN.
# * Production / ``_reject_sentinel`` defends read ``SECRET_KEY``
#   with no fallback (audit finding F-016).
# * ``app.config.BaseConfig.TOTP_ENCRYPTION_KEY`` reads the suite's
#   one two-factor key (ruling plan#3890).
#
# Setting any of these env vars after the first ``from app import ...``
# would leave the app pointed at a stale value.  So the two modules
# imported first, ``tests._fixture_profile`` and
# ``tests._worker_database``, import nothing from ``app``, and every
# fixture module after them may.

import base64
import os

import pytest

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

# The suite's ONE two-factor encryption key (ruling plan#3890), written here
# for the reason SECRET_KEY is: ``app/config.py`` reads it at class-definition
# time, as production's config does.  ``app.services.mfa_service`` reads the
# environment at each call, so every build, test, worker and run holds this one
# key, and a seeded world's owner, whose secret is encrypted once when the
# worker builds the world, decrypts in every test that clones it.  Written
# outright rather than ``setdefault``, so a key in the developer's shell or
# ``.env`` is never the run's.  A test needing another key, or none, sets or
# deletes it with ``monkeypatch``, which puts this one back after it.  A
# Fernet key is 32 bytes in url-safe base64, built here from readable bytes so
# the source holds no string the secret scan reads as a credential.
os.environ["TOTP_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(
    b"shekel-test-suite-two-factor-key",
).decode()

from tests._fixture_profile import _FIXTURE_PROFILE_ENABLED, _profile_print_summary
from tests._worker_database import (
    _ADMIN_CONNECTION,
    _BOOTSTRAP_RESULT,
    _drop_worker_database,
)
from tests._shard import SHARD_ENV, apply_shard, parse_shard
# Every fixture, imported by name: pytest registers each fixture it finds in this
# module's namespace with this conftest's directory as its base, so each keeps the
# name, scope, autouse and reach it had when it was defined here.
# Pylint: ``unused-import`` -- pytest reads every name these imports bind, and this
# module reads none of them.
# pylint: disable=unused-import
from tests._app_fixtures import (
    _calendar_sweep,
    _seeded_snapshots,
    app,
    below_the_transfer_column_drop,
    client,
    db,
    disable_hibp_check,
    each_request_has_its_own_g,
    fast_bcrypt,
    owner_client,
    seeded_world,
    setup_database,
    tax_law,
)
from tests._seed_owner_fixtures import (
    auth_client,
    bare_auth_client,
    bare_periods,
    bare_user,
    bare_user_with_cadence,
    no_two_factor_client,
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


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report():
    """Fail a collector whose tests or classes carry ``@pytest.mark.xdist_group``.

    The suite has no groups and runs under xdist's default scheduling
    (plan#2954), so the marker would keep nothing together: a test that relied
    on it would fail only when the scheduler split it from its partner, which
    is the failure pytest 9.2.0 caused (ruling plan#3768).  It is a tripwire,
    not a guarantee: nothing structural stops a new test sharing state, so the
    message says where each kind of state belongs instead.

    **A collection error, not a usage error** (ruling plan#3814).  A
    ``pytest.UsageError`` raised from ``pytest_collection_modifyitems`` says
    nothing under xdist, measured 2026-11-13 on throwaway test files: pytest
    calls ``pytest_collection_finish`` in a ``finally``, so the worker reports
    its whole collection, then dies on the error, and the controller crashes on
    xdist's own assertion naming one test.  A failed collection report is what
    xdist forwards from a worker by design.  Measured the same day, this
    wrapper's error printed under ``-n 0`` and ``-n 2`` and in shards 0/6 and
    3/6, since collection comes before the shard narrows anything.  Under
    xdist the other files' tests still run and the run ends red (exit 1), as for
    any file that fails to collect; without it the run stops (exit 2).

    Each node is read through ``get_closest_marker``, which finds a mark set on
    it, its class, its module or one of its parameters.  pytest reads a class's
    own marks only when it collects that class, so a class marked by its own
    decorator fails its own report, naming its tests; inside a file whose other
    tests are refused it shows on the run after those are fixed, since pytest
    collects nothing under a collector that failed.

    Yields:
        To pytest's own report of the collector, which comes back as the result.

    Returns:
        pytest.CollectReport: That report, or a failed one naming each test or
        class it collected that carries the marker.
    """
    report = yield
    grouped = [node.nodeid for node in report.result if node.get_closest_marker("xdist_group")]
    if not grouped:
        return report
    return pytest.CollectReport(
        report.nodeid,
        "failed",
        "The suite has no xdist_group (plan#2954), so the marker on these "
        "tests would keep nothing on one worker:\n  "
        + "\n  ".join(grouped)
        + "\nA test must hold no state another test reads: its rows in its "
        "own clone of the database, and its process-wide state in a "
        "session-scoped fixture (docs/testing-standards.md, Cluster-state "
        "and process-wide state).",
        [],
        report.sections,
    )


def pytest_collection_modifyitems(config, items):
    """Narrow the collection to one CI shard when ``SHEKEL_TEST_SHARD`` is set.

    CI splits the suite across parallel jobs (plan step ``bank_import:X-gy``);
    each exports ``SHEKEL_TEST_SHARD=<index>/<total>`` and runs only its share.
    Unset -- every local run -- this deselects nothing.  How the split is a
    partition by construction is :mod:`tests._shard`.
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


def pytest_sessionfinish():
    """Drop the per-pytest-worker database if one stands, AND emit the profile summary.

    Pytest invokes this hook at the end of every session -- including
    failed sessions.  The ``db`` fixture drops each test's database as
    the test ends and ``setup_database`` drops the bootstrap's, so on
    an ordinary session the name is already free and this drop finds
    nothing; it is there for a session that ended with a database
    standing under the name (a fixture that never reached its
    teardown).  No-op when the xdist master process skipped the
    bootstrap (``_BOOTSTRAP_RESULT`` is ``None``); only worker
    processes own a DB to drop.

    Why the worker's admin connection (not SQLAlchemy):
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
        Phase 3 for the broader context.  The connection is the one
        every clone and drop of the session went over
        (``tests/_worker_database.py``, ruling plan#3900), and it is
        closed here, after its last statement.

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

    It takes neither of the hook's arguments, since pluggy passes a hook
    only the ones it names: the cleanup keys off the module-level
    ``_BOOTSTRAP_RESULT``, not the session, and the DB is dropped
    whatever the exit status, because it is throwaway.
    """
    if _BOOTSTRAP_RESULT is not None:
        db_name, _admin_url = _BOOTSTRAP_RESULT
        # The worlds' snapshots are not dropped here: the ``_seeded_snapshots``
        # fixture's teardown drops them, as pytest tears session fixtures down.
        try:
            _drop_worker_database(db_name)
        finally:
            _ADMIN_CONNECTION.close()

    # Only the xdist controller / single-process run aggregates and
    # prints.  Workers (PYTEST_XDIST_WORKER set) are write-only: they
    # already appended their per-test rows to their own CSV during
    # the run, and the controller's pytest_sessionfinish fires after
    # every worker has exited, so by the time we read here every
    # row is on disk.
    if _FIXTURE_PROFILE_ENABLED and not os.environ.get("PYTEST_XDIST_WORKER"):
        _profile_print_summary()
