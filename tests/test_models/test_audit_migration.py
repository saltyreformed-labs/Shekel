"""Tests for the audit-infrastructure rebuild migration (Commit C-13).

Covers:

  * Idempotency of :func:`app.audit_infrastructure.apply_audit_infrastructure`.
  * Round-trip of apply + remove + apply that simulates ``flask db
    upgrade`` -> ``flask db downgrade`` -> ``flask db upgrade``.
  * The ``shekel_app`` least-privilege role and the GRANT block inside the
    migration: the role is in the cluster from its start, and granted its
    runtime posture in a test's own clone it can DML but not DDL.
  * The ``app.current_user_id`` session-variable capture path.
  * The trigger count the deploy's check asks for
    (``app.audit_infrastructure.require_audit_triggers``, entrypoint step 3).

The role is the CLUSTER's, never a test's (plan#3636).
``scripts/build_test_db_image.py`` creates it in the baked image before the
template is built, as the entrypoint creates it before it migrates, so no test
creates or drops it.  A test's grants to it live in that test's clone, which
the next test's setup drops with them.  The tests reach the role by ``SET
ROLE``, which the suite's connecting role may do as the image's superuser.
"""
# pylint: disable=redefined-outer-name  -- pytest fixture pattern
from __future__ import annotations

from datetime import date
from decimal import Decimal

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
from tests._stored_ledger import ledger_accounts_for_account
from tests._test_helpers import (
    add_entry,
    create_account_of_type,
    create_envelope_txn,
    make_balanced_entry,
    one_off_row_of,
)


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
    failure.  No later test needs that: conftest's ``db`` fixture
    gives every test its own clone of the template, so a partial run
    leaves only its own clone without triggers.
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
    """Grant the cluster's ``shekel_app`` role its runtime posture in this test's clone.

    The role is the cluster's from its start (the module docstring), so this
    neither creates nor drops it.  It gives the role, in this test's own
    database, the grants ``scripts/init_db_role.sql`` gives it on every
    container start on the schemas these tests exercise, and re-closes the
    ledger through the shared SQL that file mirrors.  It copies those grants
    rather than running the file, so it carries none of the file's
    ``ALTER DEFAULT PRIVILEGES``; plan#3771 is the step that runs the file.
    Nothing is revoked afterwards: the grants live in the clone, which the next
    test's setup drops with them.

    Returns:
        The role's name, for ``SET ROLE`` and the privilege functions.
    """
    role_name = "shekel_app"
    # Mirror init_db_role.sql for the schemas the test exercises.  The GRANT
    # CONNECT must name the live database; current_database() avoids
    # hard-coding which clone this test is connected to.
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
    return role_name


class TestTheRoleIsTheClustersFromItsStart:
    """The baked cluster held ``shekel_app`` before its template was built (plan#3636).

    Production creates the role at entrypoint step 2b, before step 3 migrates,
    so every migration and re-applied grant there takes its role-present
    branch.  The bake does the same, which is what this grades.
    """

    def test_a_fresh_clone_already_grants_the_audit_log_to_the_role(self, db):
        """Before any fixture grants, the role holds the audit log's grants.

        Only the role-present branch of ``_GRANT_APP_ROLE_SQL`` writes them,
        and nothing runs it in a clone before a test starts, so a fresh clone
        holding them was cloned from a template built while the role existed.
        A role created after the template build leaves all four ``False``;
        no role at all fails the query (``role "shekel_app" does not exist``).
        """
        row = db.session.execute(db.text(
            "SELECT has_schema_privilege(:r, 'system', 'USAGE'), "
            "  has_table_privilege(:r, 'system.audit_log', 'SELECT'), "
            "  has_table_privilege(:r, 'system.audit_log', 'INSERT'), "
            "  has_sequence_privilege(:r, 'system.audit_log_id_seq', 'USAGE')"
        ), {"r": "shekel_app"}).one()
        assert tuple(row) == (True, True, True, True)


class TestLeastPrivilegeRole:
    """Verify the GRANT block inside the migration is correctly scoped.

    The role tests exercise the cluster's ``shekel_app`` role (the
    canonical role ``scripts/init_db_role.sql`` provisions in
    production), granted its runtime posture by :func:`shekel_app_role`,
    so the DML/DDL boundary asserted here matches the runtime posture
    exactly.  ``SET ROLE`` switches the current transaction's privilege
    check to the named role -- requires the test runner to be either a
    superuser or a member of the role.
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
    fixture, which grants the role exactly as ``init_db_role.sql`` does
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
