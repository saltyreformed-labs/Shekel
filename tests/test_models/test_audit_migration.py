"""Tests for the audit-infrastructure rebuild migration (Commit C-13).

Covers:

  * Idempotency of :func:`app.audit_infrastructure.apply_audit_infrastructure`.
  * Round-trip of apply + remove + apply that simulates ``flask db
    upgrade`` -> ``flask db downgrade`` -> ``flask db upgrade``.
  * Provisioning of the ``shekel_app`` least-privilege role and the
    GRANT block inside the migration -- exercised by creating the
    role within the test, asserting the role can DML but not DDL,
    then dropping it.
  * The ``app.current_user_id`` session-variable capture path.
  * The trigger count the deploy's check asks for
    (``app.audit_infrastructure.require_audit_triggers``, entrypoint step 3).

The tests lean on the ``shekel_user`` test role being a PostgreSQL
superuser (which it is on this project's local dev/test config) so
the role-provisioning paths can run without a separate fixture
container.  Tests that need that capability assert it up front via
``ROLE_PROVISIONING_AVAILABLE`` so a future hardened test
environment skips them cleanly instead of failing.
"""
# pylint: disable=redefined-outer-name  -- pytest fixture pattern
from __future__ import annotations

import os
import time
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from sqlalchemy.exc import ProgrammingError

from app.audit_infrastructure import (
    EXPECTED_TRIGGER_COUNT,
    apply_audit_infrastructure,
    remove_audit_infrastructure,
)
from app.enums import PostingKindEnum, PostingSourceEnum
from app.extensions import db
from app.models.journal_entry import JournalEntry, Posting
from app.models.pay_period import PayPeriod
from app.models.ref import AccountType
from app.posting_infrastructure import apply_ledger_append_only_privileges
from app.services import account_service
from tests._test_helpers import (
    add_entry,
    create_account_of_type,
    create_envelope_txn,
    ledger_accounts_for_account,
    make_balanced_entry,
    one_off_row_of,
)


# Module-level xdist_group marker pins every test in this module to
# a single pytest-xdist worker (under ``--dist=loadgroup``, set in
# ``pytest.ini``).  Pinning is required because the cluster-scoped
# ``shekel_app`` PostgreSQL role created by the
# :func:`shekel_app_role` fixture cannot be safely concurrent with
# tests in OTHER classes of this same file -- specifically
# ``TestApplyIdempotent`` and ``TestRoundTrip`` invoke
# :func:`apply_audit_infrastructure`, whose ``_GRANT_APP_ROLE_SQL``
# block conditionally ``GRANT``s privileges to ``shekel_app`` WHEN
# the role exists.  If those tests ran on a different worker while
# ``TestLeastPrivilegeRole`` had the role alive, the GRANTs would
# leak into the OTHER worker's per-session database; the
# ``shekel_app_role`` fixture's teardown ``DROP OWNED BY`` only
# affects its own database, so the cross-database grants would
# block ``DROP ROLE`` with ``DependentObjectsStillExist`` errors.
# Pinning the entire module sidesteps that race; other test files
# continue to fan out across workers normally.
pytestmark = pytest.mark.xdist_group("shekel_app_role")

#: Where ``conftest`` connects to create and drop each worker's database,
#: and therefore the one place every test process on this postmaster
#: shares.  Same default and same environment variable, so the lock cannot
#: end up in a different cluster from the databases it is guarding.
_DEFAULT_ADMIN_URL = "postgresql:///postgres"


#: The advisory-lock key that serialises the ``shekel_app`` role ACROSS
#: PROCESSES.  Any 64-bit constant works; this one is arbitrary and stable.
#:
#: **``xdist_group`` above is not enough, and the gap is a whole class of
#: shared state the project's isolation knobs could not reach** (measured
#: 2026-08-22).  That marker pins this module to one worker WITHIN a run.  A
#: PostgreSQL role is a CLUSTER object, so it is shared by every database on the
#: postmaster -- and the per-checkout prefix that was how two checkouts isolated
#: themselves renamed DATABASES.  Two suites running side by side therefore
#: tore this role out from under each other whatever prefix they used.
#:
#: **Since ``balance:X-br-4`` the lock guards a state the SANCTIONED DOOR can no
#: longer reach.**  ``scripts/test.sh`` gives every run a private cluster, so two
#: suites no longer share a postmaster and the role is per-run by construction;
#: CI runs one suite on its own throwaway service.  Only a bare ``pytest``
#: against some shared cluster still reaches the hazard.
#:
#: **It is kept on COST, and the distinction matters because the same step
#: DELETED a fence on the opposite reasoning.**  ``TEST_DB_PREFIX`` went
#: precisely because only a bare ``pytest`` could still want it, so "a bare
#: pytest could still hit this" cannot by itself be why the lock stays.  What
#: separates them is price: the prefix cost a live environment read in three
#: files that had to agree, while this lock is one advisory acquisition on a
#: connection the fixture opens anyway, and it fails with a sentence naming the
#: cause instead of a bare per-test timeout.  Deleting it is a reasonable
#: follow-up; do not read its presence as evidence the hazard is live.
#:
#: Reproduced deterministically: two prefixed runs of this one module in
#: parallel produce 2-4 setup ERRORS on each side, every one an
#: ``@shekel_app_role`` fixture.  In a full-suite run it surfaces as a single
#: unexplained ``ERROR`` that passes on re-run alone -- which reads exactly like
#: a code regression and cost a session an hour.  It reproduces identically on
#: commits either side of plan step X-az, so it is not that step's.
#:
#: **The role may not simply be renamed per process**, and that is why the lock
#: is the fix rather than a suffix: ``app.posting_infrastructure`` names
#: ``shekel_app`` as a LITERAL in the GRANT / REVOKE SQL the fixture itself runs
#: (``apply_ledger_append_only_privileges``), so a renamed role would make the
#: app's own posture SQL a silent no-op and every privilege assertion below
#: would grade nothing.  The resource is cluster-scoped, so the mutex has to be.
_ROLE_LOCK_KEY = 782_301_559_004_115


def _take_the_role_lock(timeout_seconds: int = 30):
    """Take the cross-process lock guarding the ``shekel_app`` role.

    **On the ADMIN database, and that is the whole mechanism** (measured
    2026-08-22, twice).  Two earlier shapes did not work and each was wrong in
    an instructive way:

    * a lock taken through ``db.session`` is lost at the fixture's first
      ``commit()``, because a session-level advisory lock is held by a
      CONNECTION and the session returns its connection to the pool there;
    * a lock taken on the test's OWN database excludes nobody, because
      **PostgreSQL advisory locks are namespaced per DATABASE**.  Two suites
      sitting in differently-named databases on one postmaster -- which is what
      the per-checkout prefix produced -- take the same key in two different
      lock spaces and both win.

    The role is a CLUSTER object, so the mutex has to live somewhere every
    process on the postmaster shares.  ``TEST_ADMIN_DATABASE_URL`` is that
    place -- it is the connection ``conftest`` already uses to create and drop
    each worker's database, so it is guaranteed reachable wherever the suite
    runs.  A dedicated connection held for the fixture's whole span makes the
    create-use-drop sequence atomic; closing it releases the lock, so no unlock
    statement can be skipped by an exception.

    **It polls with a deadline rather than blocking**, so a peer that holds the
    lock produces a sentence naming the cause instead of the suite's per-test
    timeout naming nothing.

    Args:
        timeout_seconds: How long to wait before giving up.

    Returns:
        The open ``psycopg`` connection holding the lock.  The caller MUST
        close it.

    Raises:
        RuntimeError: When another process on this postmaster still holds it.
    """
    connection = psycopg.connect(
        os.environ.get("TEST_ADMIN_DATABASE_URL", _DEFAULT_ADMIN_URL),
    )
    connection.autocommit = True
    deadline = time.monotonic() + timeout_seconds
    while True:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_lock(%s)", (_ROLE_LOCK_KEY,),
            )
            if cursor.fetchone()[0]:
                return connection
        if time.monotonic() >= deadline:
            connection.close()
            raise RuntimeError(
                "another test process on this postmaster holds the "
                "cluster-wide 'shekel_app' role. A PostgreSQL role is a "
                "CLUSTER object, so naming the DATABASES apart cannot isolate "
                "it and two suites on one postmaster drop it out from under "
                "each other. ./scripts/test.sh gives every run a private "
                "cluster, so reaching this means something shares a "
                "postmaster with you -- a bare pytest, most likely. Wait for "
                "the peer run to finish, or run this module alone."
            )
        time.sleep(0.2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _trigger_count(session) -> int:
    """Count audit_* triggers attached to user tables.

    Excludes internal triggers (``tgisinternal``) to avoid counting
    PostgreSQL's own RI/check trigger machinery, which would inflate
    the number well past EXPECTED_TRIGGER_COUNT.
    """
    return session.execute(db.text(
        "SELECT count(*) FROM pg_trigger "
        "WHERE tgname LIKE 'audit_%' AND NOT tgisinternal"
    )).scalar()


def _audit_log_table_exists(session) -> bool:
    """Return True iff ``system.audit_log`` is present in the catalog."""
    return session.execute(db.text(
        "SELECT EXISTS ("
        "  SELECT 1 FROM information_schema.tables "
        "  WHERE table_schema = 'system' AND table_name = 'audit_log'"
        ")"
    )).scalar()


def _audit_function_exists(session) -> bool:
    """Return True iff ``system.audit_trigger_func`` is present."""
    return session.execute(db.text(
        "SELECT EXISTS ("
        "  SELECT 1 FROM pg_proc p "
        "  JOIN pg_namespace n ON p.pronamespace = n.oid "
        "  WHERE n.nspname = 'system' "
        "    AND p.proname = 'audit_trigger_func'"
        ")"
    )).scalar()


def _is_superuser(session) -> bool:
    """Whether the connected role can run CREATE ROLE / GRANT."""
    row = session.execute(db.text(
        "SELECT rolsuper OR rolcreaterole AS can_provision "
        "FROM pg_roles WHERE rolname = current_user"
    )).first()
    return bool(row.can_provision) if row else False


@pytest.fixture
def session_executor(db):  # noqa: ARG001  -- db is the conftest fixture
    """Return a callable suitable as ``apply_audit_infrastructure(executor)``.

    The closure runs each statement on the test session.  Tests must
    commit explicitly when they want the change to persist past the
    surrounding transaction.
    """
    return lambda sql: db.session.execute(db.text(sql))


# ---------------------------------------------------------------------------
# Idempotency tests
# ---------------------------------------------------------------------------


class TestApplyIdempotent:
    """Re-running ``apply_audit_infrastructure`` is a no-op.

    The second call must not raise (CREATE TABLE IF NOT EXISTS,
    CREATE OR REPLACE FUNCTION, DROP TRIGGER IF EXISTS + CREATE
    TRIGGER) and must not change the trigger count.  Both behaviours
    matter because the function runs on every container start (via
    init_database.py for fresh DBs) and at session start in the test
    suite.
    """

    def test_second_apply_does_not_raise(self, db, session_executor):
        """Calling apply twice in a row succeeds without errors.

        The conftest setup already calls apply once; this test calls
        it a second time and asserts the call returns cleanly.
        """
        apply_audit_infrastructure(session_executor)
        db.session.commit()
        # No exception raised; the test passes by reaching this line.

    def test_second_apply_preserves_trigger_count(
        self, db, session_executor
    ):
        """Trigger count after a second apply equals EXPECTED_TRIGGER_COUNT.

        DROP TRIGGER IF EXISTS + CREATE TRIGGER inside apply is
        idempotent; the count must not double or change shape.
        """
        before = _trigger_count(db.session)
        assert before == EXPECTED_TRIGGER_COUNT, (
            "conftest setup did not produce the expected trigger "
            "count -- the rest of this test is meaningless until "
            "that fixture is fixed."
        )
        apply_audit_infrastructure(session_executor)
        db.session.commit()
        after = _trigger_count(db.session)
        assert after == before


# ---------------------------------------------------------------------------
# Round-trip tests (simulates upgrade -> downgrade -> upgrade)
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """Apply -> remove -> re-apply restores the original state.

    Mirrors ``flask db upgrade`` -> ``flask db downgrade`` ->
    ``flask db upgrade`` for the rebuild migration.  The test wraps
    its work in a try/finally that re-applies the infrastructure on
    failure so a partial run does not leave subsequent tests without
    triggers.  conftest's ``db`` fixture does not re-create the
    audit infrastructure between tests (it is session-scoped) so the
    cleanup matters.
    """

    def test_remove_drops_table_function_and_triggers(
        self, db, session_executor
    ):
        """remove_audit_infrastructure drops every artefact.

        After the call: zero ``audit_*`` triggers, no
        ``system.audit_log`` table, no ``system.audit_trigger_func``.
        """
        try:
            remove_audit_infrastructure(session_executor)
            db.session.commit()
            assert _trigger_count(db.session) == 0
            assert not _audit_log_table_exists(db.session)
            assert not _audit_function_exists(db.session)
        finally:
            apply_audit_infrastructure(session_executor)
            db.session.commit()

    def test_apply_after_remove_restores_state(
        self, db, session_executor
    ):
        """Re-apply produces an identical end state to the initial setup."""
        try:
            remove_audit_infrastructure(session_executor)
            db.session.commit()

            apply_audit_infrastructure(session_executor)
            db.session.commit()

            assert _trigger_count(db.session) == EXPECTED_TRIGGER_COUNT
            assert _audit_log_table_exists(db.session)
            assert _audit_function_exists(db.session)
        finally:
            # If the assertions above failed mid-way, ensure the
            # infrastructure is fully restored before the next test.
            apply_audit_infrastructure(session_executor)
            db.session.commit()

    def test_full_round_trip_preserves_audit_capture(
        self, db, session_executor, seed_user
    ):
        """After remove + apply, the trigger still captures inserts.

        Verifies the round trip does not silently leave the function
        body in a degraded state where INSERTs succeed but no audit
        row is written.
        """
        try:
            remove_audit_infrastructure(session_executor)
            db.session.commit()
            apply_audit_infrastructure(session_executor)
            db.session.commit()

            # Trigger an INSERT on a re-instrumented table.
            checking_type = (
                db.session.query(AccountType).filter_by(name="Checking").one()
            )
            new_account = account_service.create_account(
                account_service.AccountSpec(
                    user_id=seed_user["user"].id,
                    account_type_id=checking_type.id,
                    name="Round Trip Account",
                    anchor_balance=Decimal("0.00"),
                ),
            )
            db.session.add(new_account)
            db.session.flush()

            audit_rows = db.session.execute(db.text(
                "SELECT * FROM system.audit_log "
                "WHERE table_name = 'accounts' "
                "  AND operation = 'INSERT' "
                "  AND new_data->>'name' = 'Round Trip Account'"
            )).fetchall()
            assert len(audit_rows) == 1
        finally:
            apply_audit_infrastructure(session_executor)
            db.session.commit()


# ---------------------------------------------------------------------------
# user_id capture
# ---------------------------------------------------------------------------


class TestUserIdCapture:
    """The trigger reads ``app.current_user_id`` from session state.

    Complements the integration tests that drive this through the
    Flask before_request hook by exercising the SET LOCAL path in
    isolation -- if the trigger function regresses, this test
    catches it without requiring the whole HTTP stack.
    """

    def test_set_local_propagates_to_audit_row(
        self, db, seed_user, seed_periods
    ):
        """A transaction-local user id set before INSERT shows up in audit_log.user_id.

        Set the way the application sets it (``app.audit_infrastructure``'s
        ``set_config(..., true)``, ``SET LOCAL``'s function form).  ``SET LOCAL
        ... = :uid`` itself cannot carry a bound parameter under psycopg 3's
        server-side binding (plan step balance:X-dj, ruling R-BAL210).
        """
        # pylint: disable=import-outside-toplevel
        from app.models.ref import TransactionType

        expense = (
            db.session.query(TransactionType).filter_by(name="Expense").one()
        )

        db.session.execute(
            db.text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": str(seed_user["user"].id)},
        )

        one_off_row_of(
            seed_periods[0],
            name="UID Capture Test",
            amount=Decimal("12.00"),
            user_id=seed_periods[0].user_id,
            account_id=seed_user["account"].id,
            scenario_id=seed_user["scenario"].id,
            transaction_type_id=expense.id,
            category_id=seed_user["categories"]["Rent"].id,
        )
        db.session.flush()

        rows = db.session.execute(db.text(
            "SELECT user_id FROM system.audit_log "
            "WHERE table_name = 'transactions' "
            "  AND operation = 'INSERT' "
            "  AND new_data->>'name' = 'UID Capture Test'"
        )).fetchall()
        assert len(rows) == 1
        assert rows[0].user_id == seed_user["user"].id


# ---------------------------------------------------------------------------
# Least-privilege role tests
# ---------------------------------------------------------------------------


@pytest.fixture
def shekel_app_role(db):
    """Create + configure the ``shekel_app`` role for the duration of one test.

    Skips the test on a non-superuser test role.  Wraps creation in
    a try/finally so the role drops even when the inner assertions
    fail; matching ``DROP OWNED BY`` removes any per-test grants
    that would otherwise survive across tests.
    """
    if not _is_superuser(db.session):
        pytest.skip(
            "Test requires CREATEROLE/SUPERUSER on the test database "
            "user.  Skipped automatically on hardened CI environments."
        )

    # CLUSTER-WIDE, so the mutex is too (see :data:`_ROLE_LOCK_KEY`).  Taken
    # before the DROP below, which is the statement that takes a peer's role
    # away mid-test, and released only after this fixture's own DROP.
    lock_connection = _take_the_role_lock()

    role_name = "shekel_app"
    password = "test-shekel-app-password"
    db.session.execute(db.text(
        f"DO $$ "
        f"BEGIN "
        f"  IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{role_name}') THEN "
        f"    EXECUTE format('DROP OWNED BY {role_name} CASCADE'); "
        f"    EXECUTE 'DROP ROLE {role_name}'; "
        f"  END IF; "
        f"END$$"
    ))
    db.session.execute(db.text(
        f"CREATE ROLE {role_name} WITH LOGIN PASSWORD '{password}'"
    ))
    # Mirror init_db_role.sql for the schemas the test exercises.
    # tax DB or shekel_test DB -- the GRANT CONNECT must reference
    # the live database name.  current_database() avoids hard-coding
    # which DB the test container happens to be connected to.
    current_db = db.session.execute(
        db.text("SELECT current_database()")
    ).scalar()
    db.session.execute(db.text(
        f'GRANT CONNECT ON DATABASE "{current_db}" TO {role_name}'
    ))
    db.session.execute(db.text(
        f"GRANT USAGE ON SCHEMA auth, budget, salary, ref TO {role_name}"
    ))
    db.session.execute(db.text(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        f"ON ALL TABLES IN SCHEMA auth, budget, salary TO {role_name}"
    ))
    db.session.execute(db.text(
        f"GRANT SELECT ON ALL TABLES IN SCHEMA ref TO {role_name}"
    ))
    db.session.execute(db.text(
        "GRANT USAGE "
        f"ON ALL SEQUENCES IN SCHEMA auth, budget, salary, ref TO {role_name}"
    ))
    db.session.execute(db.text(f"GRANT USAGE ON SCHEMA system TO {role_name}"))
    db.session.execute(db.text(
        f"GRANT SELECT, INSERT ON system.audit_log TO {role_name}"
    ))
    db.session.execute(db.text(
        f"GRANT USAGE ON SEQUENCE system.audit_log_id_seq TO {role_name}"
    ))
    # Mirror init_db_role.sql's ledger append-only posture (review M1/R4):
    # the blanket DML grant above just re-opened UPDATE/DELETE on the two
    # ledger tables, exactly as every container start does -- re-close it
    # through the same shared SQL the entrypoint, the fresh-DB path, and
    # migration e3c23fadb21d apply, so the role's posture here matches the
    # runtime posture bit for bit.
    apply_ledger_append_only_privileges(
        lambda statement: db.session.execute(db.text(statement))
    )
    db.session.commit()

    try:
        yield role_name
    finally:
        db.session.rollback()
        db.session.execute(db.text(
            f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA "
            f"auth, budget, salary, ref, system FROM {role_name}"
        ))
        db.session.execute(db.text(
            f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA "
            f"auth, budget, salary, ref, system FROM {role_name}"
        ))
        db.session.execute(db.text(
            f"REVOKE USAGE ON SCHEMA auth, budget, salary, ref, system "
            f"FROM {role_name}"
        ))
        db.session.execute(db.text(
            f'REVOKE CONNECT ON DATABASE "{current_db}" FROM {role_name}'
        ))
        db.session.execute(db.text(f"DROP OWNED BY {role_name} CASCADE"))
        db.session.execute(db.text(f"DROP ROLE IF EXISTS {role_name}"))
        db.session.commit()
        # LAST, and after the commit: the lock is what makes the whole
        # create-use-drop span atomic against another process, so releasing it
        # before the role is actually gone would reopen the window it closes.
        # Closing releases it, which is why there is no unlock statement to
        # skip if anything above raises.
        lock_connection.close()


class TestLeastPrivilegeRole:
    """Verify the GRANT block inside the migration is correctly scoped.

    The role tests exercise a separately-created ``shekel_app`` role
    (the canonical role provisioned by ``scripts/init_db_role.sql``
    in production) so the DML/DDL boundary asserted here matches the
    runtime posture exactly.  ``SET ROLE`` switches the current
    transaction's privilege check to the named role -- requires the
    test runner to be either a superuser or a member of the role.

    Cluster-level serialisation is handled at module scope via
    ``pytestmark`` so this class and the sibling classes that call
    :func:`apply_audit_infrastructure` are all pinned to the same
    pytest-xdist worker.  See the module-level comment above
    ``pytestmark`` for the full reasoning.
    """

    def test_app_role_can_select_audit_log(
        self, db, shekel_app_role
    ):
        """``shekel_app`` can read ``system.audit_log`` -- needed for forensics."""
        try:
            db.session.execute(db.text(f"SET ROLE {shekel_app_role}"))
            row_count = db.session.execute(
                db.text("SELECT count(*) FROM system.audit_log")
            ).scalar()
            assert row_count is not None
        finally:
            db.session.execute(db.text("RESET ROLE"))

    def test_app_role_can_dml_budget_tables(
        self, db, shekel_app_role, seed_user
    ):
        """``shekel_app`` can INSERT/UPDATE/DELETE on ``budget.*``."""
        # SQLAlchemy reuses pooled connections that may carry a
        # stale role from a prior test; RESET ROLE before SET ROLE
        # to make the boundary explicit.
        try:
            db.session.execute(db.text("RESET ROLE"))
            db.session.execute(db.text(f"SET ROLE {shekel_app_role}"))

            # INSERT.  E-19 / Commit 3 makes current_anchor_period_id
            # The anchor columns this INSERT used to supply went with
            # ruling R-EH; an account row is its owner, type and name.
            checking_type_id = db.session.execute(db.text(
                "SELECT id FROM ref.account_types WHERE name = 'Checking'"
            )).scalar()
            db.session.execute(
                db.text(
                    "INSERT INTO budget.accounts "
                    "(user_id, account_type_id, name) "
                    "VALUES (:uid, :tid, :name)"
                ),
                {
                    "uid": seed_user["user"].id,
                    "tid": checking_type_id,
                    "name": "App Role Test Account",
                },
            )

            # UPDATE
            db.session.execute(
                db.text(
                    "UPDATE budget.accounts "
                    "SET name = :new_name "
                    "WHERE name = :old_name"
                ),
                {
                    "new_name": "App Role Renamed",
                    "old_name": "App Role Test Account",
                },
            )

            # DELETE
            deleted = db.session.execute(
                db.text(
                    "DELETE FROM budget.accounts "
                    "WHERE name = 'App Role Renamed'"
                )
            ).rowcount
            assert deleted == 1
        finally:
            db.session.execute(db.text("RESET ROLE"))

    def test_app_role_cannot_drop_table(
        self, db, shekel_app_role
    ):
        """``shekel_app`` is rejected when it tries DDL such as DROP TABLE.

        PostgreSQL surfaces two distinct messages depending on which
        privilege check fires first: ``permission denied`` for
        schema-level USAGE failures and ``must be owner of table``
        when the role has USAGE but not ownership.  The test
        accepts either form because both prove the privilege escalation
        attempt was rejected.  A successful ``DROP TABLE`` would mean
        the GRANT block leaked ownership.
        """
        # pylint: disable=import-outside-toplevel
        from sqlalchemy.exc import ProgrammingError, SQLAlchemyError

        try:
            db.session.execute(db.text("RESET ROLE"))
            db.session.execute(db.text(f"SET ROLE {shekel_app_role}"))
            with pytest.raises((ProgrammingError, SQLAlchemyError)) as excinfo:
                db.session.execute(
                    db.text("DROP TABLE budget.accounts CASCADE")
                )
                # Force the implicit transaction to fail explicitly
                # so SQLAlchemy raises here rather than swallowing
                # the error until commit.
                db.session.flush()
            error_text = str(excinfo.value).lower()
            assert (
                "permission denied" in error_text
                or "must be owner" in error_text
            ), f"Unexpected error text: {error_text}"
        finally:
            db.session.rollback()
            db.session.execute(db.text("RESET ROLE"))

    def test_app_role_cannot_drop_audit_trigger(
        self, db, shekel_app_role
    ):
        """``shekel_app`` cannot DROP TRIGGER on ``budget.transactions``.

        This is the load-bearing guarantee the two-role policy
        provides: an attacker who pivots into the application role
        still cannot remove the audit trail behind their own
        actions.  A failure here means the audit posture is no
        better than it was before C-13.
        """
        # pylint: disable=import-outside-toplevel
        from sqlalchemy.exc import ProgrammingError, SQLAlchemyError

        try:
            db.session.execute(db.text("RESET ROLE"))
            db.session.execute(db.text(f"SET ROLE {shekel_app_role}"))
            with pytest.raises((ProgrammingError, SQLAlchemyError)) as excinfo:
                db.session.execute(db.text(
                    "DROP TRIGGER audit_transactions ON budget.transactions"
                ))
                db.session.flush()
            assert (
                "permission denied" in str(excinfo.value).lower()
                or "must be owner" in str(excinfo.value).lower()
            )
        finally:
            db.session.rollback()
            db.session.execute(db.text("RESET ROLE"))


# ---------------------------------------------------------------------------
# Ledger append-only privileges (balance-architecture review M1/R4)
# ---------------------------------------------------------------------------


class TestLedgerAppendOnlyPrivileges:
    """The posting ledger is append-only at the DATABASE tier for ``shekel_app``.

    The 2026-07-02 balance-architecture review demonstrated (M1) that a
    raw-SQL DELETE of one leg of a balanced entry silently breaks the trial
    balance: the deferred balanced trigger has no DELETE arm (CASCADE
    disposal would see a transient ``COUNT < 2``), its UPDATE arm checks only
    the NEW row's entry, and the ORM immutability guards do not fire for raw
    SQL.  R4 closes that surface with ``REVOKE UPDATE, DELETE`` on
    ``budget.journal_entries`` + ``budget.account_postings`` from the
    runtime role (``app.posting_infrastructure.
    apply_ledger_append_only_privileges``, applied by migration
    ``e3c23fadb21d``, ``init_database.py``, and re-asserted by
    ``init_db_role.sql`` on every container start).

    These tests exercise the posture through the :func:`shekel_app_role`
    fixture, which provisions the role exactly as ``init_db_role.sql`` does
    (blanket DML grant, then the targeted ledger revoke) -- so what is
    asserted here is the runtime posture, not a test-local approximation.
    The two disposal tests are load-bearing: PostgreSQL executes referential
    actions as the table OWNER, so tenancy CASCADE and source SET NULL must
    keep working for a role that cannot DELETE or UPDATE the ledger tables
    (verified live on the dev stack before this suite was authored).
    """

    @staticmethod
    def _owner_ledger_pair(db_session, seed_user):
        """Create a Savings account and return (checking, savings) ledger ids.

        Runs as the OWNER role (callers RESET ROLE first): test data is
        staged with owner privileges so the assertions exercise only the
        app role's runtime behaviour, not its ability to build fixtures.
        """
        savings = create_account_of_type(
            seed_user, db_session, "Savings", "R4 Posting Savings",
        )
        db_session.commit()
        checking_ledger = ledger_accounts_for_account(
            db_session, seed_user["account"].id,
        )[0].id
        savings_ledger = ledger_accounts_for_account(
            db_session, savings.id,
        )[0].id
        return checking_ledger, savings_ledger

    def test_ledger_posture_matches_runtime(self, db, shekel_app_role):
        """SELECT/INSERT stay granted; UPDATE/DELETE are revoked; targeted only.

        The last two columns are the control: ``budget.accounts`` keeps full
        DML, proving the revoke is scoped to the two append-only tables and
        did not silently widen into the posture that would break the app.
        """
        row = db.session.execute(db.text(
            "SELECT "
            "  has_table_privilege(:r, 'budget.journal_entries', 'SELECT'), "
            "  has_table_privilege(:r, 'budget.journal_entries', 'INSERT'), "
            "  has_table_privilege(:r, 'budget.journal_entries', 'UPDATE'), "
            "  has_table_privilege(:r, 'budget.journal_entries', 'DELETE'), "
            "  has_table_privilege(:r, 'budget.account_postings', 'SELECT'), "
            "  has_table_privilege(:r, 'budget.account_postings', 'INSERT'), "
            "  has_table_privilege(:r, 'budget.account_postings', 'UPDATE'), "
            "  has_table_privilege(:r, 'budget.account_postings', 'DELETE'), "
            "  has_table_privilege(:r, 'budget.accounts', 'UPDATE'), "
            "  has_table_privilege(:r, 'budget.accounts', 'DELETE')"
        ), {"r": shekel_app_role}).one()
        assert tuple(row) == (
            True, True, False, False,   # journal_entries: append-only
            True, True, False, False,   # account_postings: append-only
            True, True,                 # accounts: full DML (control)
        )

    def test_app_role_cannot_tamper_with_ledger_rows(
        self, db, seed_user, shekel_app_role,
    ):
        """All four raw-SQL tamper forms are rejected with permission denied.

        UPDATE/DELETE on each table -- the exact class the review
        demonstrated live (a single-leg DELETE broke the trial balance by
        +500.00 with nothing catching it).  After the four rejections the
        entry and both legs must be byte-identical as seen by the owner.
        """
        try:
            db.session.execute(db.text("RESET ROLE"))
            from_ledger, to_ledger = self._owner_ledger_pair(
                db.session, seed_user,
            )
            entry = make_balanced_entry(
                db.session, seed_user,
                from_ledger_id=from_ledger, to_ledger_id=to_ledger,
            )
            entry_id = entry.id
            posting_id = (
                db.session.query(Posting)
                .filter_by(journal_entry_id=entry_id)
                .first().id
            )

            tamper_attempts = (
                ("UPDATE budget.journal_entries SET description = 'tamper' "
                 "WHERE id = :i", {"i": entry_id}),
                ("DELETE FROM budget.journal_entries WHERE id = :i",
                 {"i": entry_id}),
                ("UPDATE budget.account_postings SET amount = amount + 10 "
                 "WHERE id = :i", {"i": posting_id}),
                ("DELETE FROM budget.account_postings WHERE id = :i",
                 {"i": posting_id}),
            )
            for statement, params in tamper_attempts:
                # A failed statement aborts the transaction, and the
                # rollback also unwinds SET ROLE -- re-establish both
                # per attempt.
                db.session.execute(db.text("RESET ROLE"))
                db.session.execute(db.text(f"SET ROLE {shekel_app_role}"))
                with pytest.raises(ProgrammingError) as excinfo:
                    db.session.execute(db.text(statement), params)
                    db.session.flush()
                assert "permission denied" in str(excinfo.value).lower(), (
                    f"Expected a privilege rejection for: {statement}"
                )
                db.session.rollback()

            db.session.execute(db.text("RESET ROLE"))
            db.session.expire_all()
            stored = db.session.get(JournalEntry, entry_id)
            assert stored.description == "Test entry"
            legs = (
                db.session.query(Posting)
                .filter_by(journal_entry_id=entry_id)
                .count()
            )
            assert legs == 2
        finally:
            db.session.rollback()
            db.session.execute(db.text("RESET ROLE"))

    def test_period_cascade_disposal_survives_revoke(
        self, db, seed_user, shekel_app_role,
    ):
        """Tenancy CASCADE disposes of entries + legs for the revoked role.

        The user-reachable disposal path (pay-period truncate runs as the
        app role in production): deleting a fresh, unanchored period as
        ``shekel_app`` must cascade its journal entry AND both legs even
        though the role holds neither DELETE -- PostgreSQL executes the
        referential action as the table owner.
        """
        try:
            db.session.execute(db.text("RESET ROLE"))
            from_ledger, to_ledger = self._owner_ledger_pair(
                db.session, seed_user,
            )
            fresh_period = PayPeriod(
                user_id=seed_user["user"].id,
                start_date=date(2027, 6, 4),
            )
            db.session.add(fresh_period)
            db.session.flush()
            period_id = fresh_period.id
            entry = make_balanced_entry(
                db.session, seed_user,
                from_ledger_id=from_ledger, to_ledger_id=to_ledger,
                period_id=period_id,
            )
            entry_id = entry.id

            db.session.execute(db.text(f"SET ROLE {shekel_app_role}"))
            db.session.execute(db.text(
                "DELETE FROM budget.pay_periods WHERE id = :p"
            ), {"p": period_id})
            db.session.commit()
        finally:
            db.session.rollback()
            db.session.execute(db.text("RESET ROLE"))

        db.session.expire_all()
        assert db.session.get(JournalEntry, entry_id) is None
        assert (
            db.session.query(Posting)
            .filter_by(journal_entry_id=entry_id)
            .count() == 0
        )

    def test_movement_delete_set_null_survives_revoke(
        self, db, seed_user, shekel_app_role,
    ):
        """Source SET NULL keeps working for the revoked role.

        Deleting a movement (a purchase) as ``shekel_app`` must null the
        entry's ``transaction_entry_id`` back-link -- an UPDATE of a table the
        role cannot UPDATE, executed by the referential action as the owner.
        The posted fact itself (entry + legs) survives, per the ledger's
        immutability contract.  It deleted a transaction and read the entry's
        ``transaction_id`` link until plan step ``balance:X-bi-6-5``'s second
        leaf (card plan#1845) deleted that link; the movement link is the one
        a source delete can null now.
        """
        try:
            db.session.execute(db.text("RESET ROLE"))
            from_ledger, to_ledger = self._owner_ledger_pair(
                db.session, seed_user,
            )
            envelope = create_envelope_txn(
                seed_user, db.session, seed_user["bootstrap_period"],
                "R4 set-null probe", Decimal("50.00"),
            )
            add_entry(
                db.session, seed_user, envelope, Decimal("20.00"),
                seed_user["bootstrap_period"].start_date,
            )
            db.session.commit()
            [movement] = envelope.entries
            movement_id = movement.id
            entry = make_balanced_entry(
                db.session, seed_user,
                from_ledger_id=from_ledger, to_ledger_id=to_ledger,
                transaction_entry_id=movement_id,
                source_kind=PostingSourceEnum.PURCHASE,
                posting_kind=PostingKindEnum.EXPENSE,
            )
            entry_id = entry.id

            db.session.execute(db.text(f"SET ROLE {shekel_app_role}"))
            db.session.execute(db.text(
                "DELETE FROM budget.transaction_entries WHERE id = :e"
            ), {"e": movement_id})
            db.session.commit()
        finally:
            db.session.rollback()
            db.session.execute(db.text("RESET ROLE"))

        db.session.expire_all()
        stored = db.session.get(JournalEntry, entry_id)
        assert stored is not None, "the posted fact must survive its source"
        assert stored.transaction_entry_id is None
        assert (
            db.session.query(Posting)
            .filter_by(journal_entry_id=entry_id)
            .count() == 2
        )


# ---------------------------------------------------------------------------
# Health check (matches the deploy's check, entrypoint step 3)
# ---------------------------------------------------------------------------


class TestEntrypointHealthCheck:
    """Mirror the post-migration check the deploy runs.

    ``app.audit_infrastructure.require_audit_triggers`` counts the
    ``audit_*`` triggers inside entrypoint step 3's one transaction
    (plan step balance:X-cv; it was a shell step 7 before) and compares
    against ``EXPECTED_TRIGGER_COUNT`` from the shared module.  If the
    test below fails, the production check would also fail and refuse
    the deploy -- which is the intended behaviour, but it is easier to
    debug a broken test than a refused deploy.  The count here is
    spelled independently of the deploy's on purpose.
    """

    def test_count_meets_expected(self, db):
        """Live trigger count is at least EXPECTED_TRIGGER_COUNT.

        ``>=`` rather than ``==`` so adding extra triggers in a
        future migration (e.g. for the read-audit instrumentation
        in C-52) cannot break this test; the deploy's check uses
        the same comparator.
        """
        actual = _trigger_count(db.session)
        assert actual >= EXPECTED_TRIGGER_COUNT, (
            "Audit trigger health check would fail at container "
            f"start: expected >= {EXPECTED_TRIGGER_COUNT}, found {actual}."
        )
