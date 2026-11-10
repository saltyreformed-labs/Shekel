"""Tests for PostgreSQL audit trigger system (Phase 8B WU-1).

Verifies that INSERT, UPDATE, and DELETE operations on audited tables
produce the expected rows in system.audit_log, with correct metadata,
old/new data capture, and changed-field detection.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.audit_infrastructure import AUDITED_TABLES, EXPECTED_TRIGGER_COUNT
from app.extensions import db
from app.models.salary_profile import SalaryProfile
from app.models.ref import Status, TransactionType
import pytest
from tests._test_helpers import one_off_row_of, start_test_pay_list


def _get_audit_rows(table_name=None, operation=None):
    """Query system.audit_log with optional filters.

    Returns a list of dicts with all audit_log columns.
    """
    query = "SELECT * FROM system.audit_log WHERE 1=1"
    params = {}
    if table_name:
        query += " AND table_name = :table_name"
        params["table_name"] = table_name
    if operation:
        query += " AND operation = :operation"
        params["operation"] = operation
    query += " ORDER BY id"
    result = db.session.execute(db.text(query), params)
    return [dict(row._mapping) for row in result]


def _create_transaction(seed_user, seed_periods):
    """Helper: create a minimal transaction and return it."""
    expense = db.session.query(TransactionType).filter_by(name="Expense").one()
    txn = one_off_row_of(
        seed_periods[0],
        name="Test Expense",
        amount=Decimal("100.00"),
        user_id=seed_periods[0].user_id,
        account_id=seed_user["account"].id,
        scenario_id=seed_user["scenario"].id,
        transaction_type_id=expense.id,
        category_id=seed_user["categories"]["Rent"].id,
    )
    db.session.flush()
    return txn


# ── INSERT Tests ──────────────────────────────────────────────────────────


class TestAuditTriggerInsert:
    """Verify audit_log rows are created on INSERT."""

    def test_insert_transaction_creates_audit_row(
        self, app, db, seed_user, seed_periods
    ):
        """INSERT on budget.transactions produces an audit_log row."""
        _create_transaction(seed_user, seed_periods)
        rows = _get_audit_rows("transactions", "INSERT")
        # seed_user fixture creates user/account/etc which also trigger
        # audit rows, so filter specifically for transactions.
        # Exactly 1 INSERT on transactions from _create_transaction
        assert len(rows) == 1
        txn_row = rows[-1]
        assert txn_row["operation"] == "INSERT"
        assert txn_row["table_schema"] == "budget"

    def test_insert_captures_new_data(
        self, app, db, seed_user, seed_periods
    ):
        """INSERT audit_log row contains the full new row as JSONB."""
        txn = _create_transaction(seed_user, seed_periods)
        rows = _get_audit_rows("transactions", "INSERT")
        txn_row = rows[-1]
        assert txn_row["new_data"] is not None
        assert txn_row["new_data"]["name"] == "Test Expense"
        assert txn_row["new_data"]["id"] == txn.id

    def test_insert_old_data_is_null(
        self, app, db, seed_user, seed_periods
    ):
        """INSERT audit_log row has old_data=NULL."""
        _create_transaction(seed_user, seed_periods)
        rows = _get_audit_rows("transactions", "INSERT")
        assert rows[-1]["old_data"] is None

    def test_insert_captures_row_id(
        self, app, db, seed_user, seed_periods
    ):
        """INSERT audit_log row captures the row's id."""
        txn = _create_transaction(seed_user, seed_periods)
        rows = _get_audit_rows("transactions", "INSERT")
        assert rows[-1]["row_id"] == txn.id

    def test_insert_on_account_creates_audit_row(self, app, db, seed_user):
        """INSERT on budget.accounts produces an audit_log row."""
        # seed_user already creates an account, so check for it.
        rows = _get_audit_rows("accounts", "INSERT")
        # seed_user creates exactly 1 account via INSERT
        assert len(rows) == 1
        assert rows[-1]["table_schema"] == "budget"

    def test_insert_on_salary_profile_creates_audit_row(
        self, app, db, seed_user
    ):
        """INSERT on salary.salary_profiles produces an audit_log row."""
        from app.models.ref import FilingStatus
        filing = db.session.query(FilingStatus).first()
        profile = SalaryProfile(
            period_end_days_before_payday=6,
            user_id=seed_user["user"].id,
            scenario_id=seed_user["scenario"].id,
            name="Test Salary",
            filing_status_id=filing.id,
        )
        db.session.add(profile)
        db.session.flush()
        start_test_pay_list(profile, Decimal("3076.92"))  # $80,000.00 a year / 26
        rows = _get_audit_rows("salary_profiles", "INSERT")
        # Exactly 1 INSERT from the profile creation above
        assert len(rows) == 1
        assert rows[-1]["table_schema"] == "salary"

    def test_insert_on_auth_user_settings_creates_audit_row(
        self, app, db, seed_user
    ):
        """INSERT on auth.user_settings produces an audit_log row."""
        # seed_user creates UserSettings, so check for it.
        rows = _get_audit_rows("user_settings", "INSERT")
        # seed_user creates exactly 1 UserSettings row
        assert len(rows) == 1
        assert rows[-1]["table_schema"] == "auth"


# ── UPDATE Tests ──────────────────────────────────────────────────────────


class TestAuditTriggerUpdate:
    """Verify audit_log rows are created on UPDATE with changed fields."""

    def test_update_transaction_creates_audit_row(
        self, app, db, seed_user, seed_periods
    ):
        """UPDATE on budget.transactions produces an audit_log row."""
        txn = _create_transaction(seed_user, seed_periods)
        # The ROW's own column: a one-off's figure is its definition's, and
        # detaching the row OWN is a state no door writes (R-BAL37); the
        # trigger fires on any UPDATE of the row.
        txn.notes = "audited"
        db.session.flush()
        rows = _get_audit_rows("transactions", "UPDATE")
        # Exactly 1 UPDATE from changing notes
        assert len(rows) == 1

    def test_update_captures_changed_fields(
        self, app, db, seed_user, seed_periods
    ):
        """UPDATE audit_log row lists only the columns that changed."""
        txn = _create_transaction(seed_user, seed_periods)
        db.session.commit()
        # Clear any pending audit rows from the insert.
        txn.name = "Updated Expense"
        db.session.flush()
        rows = _get_audit_rows("transactions", "UPDATE")
        last = rows[-1]
        assert "name" in last["changed_fields"]
        # estimated_amount was NOT changed, so it should not be listed.
        assert "estimated_amount" not in last["changed_fields"]

    def test_update_captures_old_and_new_data(
        self, app, db, seed_user, seed_periods
    ):
        """UPDATE audit_log row contains both old_data and new_data."""
        txn = _create_transaction(seed_user, seed_periods)
        db.session.commit()
        txn.name = "Changed Name"
        db.session.flush()
        rows = _get_audit_rows("transactions", "UPDATE")
        last = rows[-1]
        assert last["old_data"] is not None
        assert last["new_data"] is not None
        assert last["old_data"]["name"] == "Test Expense"
        assert last["new_data"]["name"] == "Changed Name"

    def test_update_no_change_skips_audit(
        self, app, db, seed_user, seed_periods
    ):
        """UPDATE with no actual value changes does not create an audit row."""
        txn = _create_transaction(seed_user, seed_periods)
        db.session.commit()
        count_before = len(_get_audit_rows("transactions", "UPDATE"))
        # Execute an UPDATE that sets the same value.
        db.session.execute(
            db.text(
                "UPDATE budget.transactions SET name = name WHERE id = :id"
            ),
            {"id": txn.id},
        )
        db.session.flush()
        count_after = len(_get_audit_rows("transactions", "UPDATE"))
        assert count_after == count_before

    def test_update_on_account_creates_audit_row(self, app, db, seed_user):
        """UPDATE on budget.accounts produces an audit_log row."""
        account = seed_user["account"]
        account.name = "Updated Checking"
        db.session.flush()
        rows = _get_audit_rows("accounts", "UPDATE")
        # Exactly 1 UPDATE from changing account name
        assert len(rows) == 1


# ── DELETE Tests ──────────────────────────────────────────────────────────


class TestAuditTriggerDelete:
    """Verify audit_log rows are created on DELETE."""

    def test_delete_transaction_creates_audit_row(
        self, app, db, seed_user, seed_periods
    ):
        """DELETE on budget.transactions produces an audit_log row."""
        txn = _create_transaction(seed_user, seed_periods)
        db.session.commit()
        db.session.execute(
            db.text("DELETE FROM budget.transactions WHERE id = :id"),
            {"id": txn.id},
        )
        db.session.flush()
        rows = _get_audit_rows("transactions", "DELETE")
        # Exactly 1 DELETE from the raw SQL delete above
        assert len(rows) == 1

    def test_delete_captures_old_data(
        self, app, db, seed_user, seed_periods
    ):
        """DELETE audit_log row contains the deleted row as old_data."""
        txn = _create_transaction(seed_user, seed_periods)
        txn_id = txn.id
        db.session.commit()
        db.session.execute(
            db.text("DELETE FROM budget.transactions WHERE id = :id"),
            {"id": txn_id},
        )
        db.session.flush()
        rows = _get_audit_rows("transactions", "DELETE")
        last = rows[-1]
        assert last["old_data"] is not None
        assert last["old_data"]["id"] == txn_id
        assert last["old_data"]["name"] == "Test Expense"

    def test_delete_new_data_is_null(
        self, app, db, seed_user, seed_periods
    ):
        """DELETE audit_log row has new_data=NULL."""
        txn = _create_transaction(seed_user, seed_periods)
        db.session.commit()
        db.session.execute(
            db.text("DELETE FROM budget.transactions WHERE id = :id"),
            {"id": txn.id},
        )
        db.session.flush()
        rows = _get_audit_rows("transactions", "DELETE")
        assert rows[-1]["new_data"] is None


# ── Metadata Tests ────────────────────────────────────────────────────────


class TestAuditTriggerMetadata:
    """Verify metadata fields on audit_log rows."""

    def test_table_schema_and_name_captured(
        self, app, db, seed_user, seed_periods
    ):
        """audit_log row captures the correct table_schema and table_name."""
        _create_transaction(seed_user, seed_periods)
        rows = _get_audit_rows("transactions", "INSERT")
        last = rows[-1]
        assert last["table_schema"] == "budget"
        assert last["table_name"] == "transactions"

    @pytest.mark.server_clock
    def test_executed_at_is_populated(
        self, app, db, seed_user, seed_periods
    ):
        """audit_log row has a recent executed_at timestamp from PG now()."""
        _create_transaction(seed_user, seed_periods)
        rows = _get_audit_rows("transactions", "INSERT")
        executed_at = rows[-1]["executed_at"]
        assert executed_at is not None, "executed_at was not set by trigger"
        # PG trigger uses now(); verify it's within 5s of current time
        now = datetime.now(timezone.utc)
        if executed_at.tzinfo is None:
            now = datetime.utcnow()
        delta = abs(now - executed_at)
        assert delta < timedelta(seconds=5), (
            f"executed_at is {delta} from now, expected < 5s"
        )

    def test_db_user_is_populated(
        self, app, db, seed_user, seed_periods
    ):
        """audit_log row captures the PostgreSQL db_user matching the test config role."""
        _create_transaction(seed_user, seed_periods)
        rows = _get_audit_rows("transactions", "INSERT")
        # Query the actual PG role from the live connection so the test
        # works regardless of which user TEST_DATABASE_URL specifies
        # ('shekel_user' locally, 'shekel_test' in CI).
        actual_db_user = db.session.execute(
            db.text("SELECT current_user")
        ).scalar()
        assert rows[-1]["db_user"] == actual_db_user

    def test_user_id_is_null_without_middleware(
        self, app, db, seed_user, seed_periods
    ):
        """Without SET LOCAL, user_id in audit_log is NULL."""
        # Tests run without the middleware setting app.current_user_id
        # via the before_request hook (direct db operations), so user_id
        # should be NULL.
        _create_transaction(seed_user, seed_periods)
        rows = _get_audit_rows("transactions", "INSERT")
        assert rows[-1]["user_id"] is None


# ── Trigger Attachment Verification ───────────────────────────────────────


class TestAuditTriggerAllTables:
    """Verify triggers are attached to every table named in :data:`AUDITED_TABLES`.

    The expected list is sourced from ``app.audit_infrastructure``
    (the same module the migration and conftest delegate to) so a
    future schema addition that forgets to update the canonical list
    cannot pass this test by accident.
    """

    def test_trigger_exists_on_all_audited_tables(self, app, db):
        """Every entry in AUDITED_TABLES has an audit trigger attached.

        Walks the full canonical list rather than a hard-coded subset
        so a forgotten table fails this test loudly.  Trigger names
        must match the ``audit_<table>`` convention enforced by
        :func:`app.audit_infrastructure._trigger_sql_for_table`.
        """
        result = db.session.execute(db.text("""
            SELECT n.nspname AS schema_name, c.relname AS table_name
            FROM pg_trigger t
            JOIN pg_class c ON t.tgrelid = c.oid
            JOIN pg_namespace n ON c.relnamespace = n.oid
            WHERE t.tgname LIKE 'audit_%'
              AND NOT t.tgisinternal
            ORDER BY n.nspname, c.relname
        """))
        triggered = {
            (row.schema_name, row.table_name) for row in result
        }
        for schema, table in AUDITED_TABLES:
            assert (schema, table) in triggered, (
                f"Missing audit trigger on {schema}.{table}; "
                "add the table to AUDITED_TABLES in "
                "app/audit_infrastructure.py and re-run "
                "the rebuild migration."
            )

    def test_trigger_count_matches_expected(self, app, db):
        """Total ``audit_*`` trigger count equals EXPECTED_TRIGGER_COUNT.

        Mirrors the count the deploy's check
        (``app.audit_infrastructure.require_audit_triggers``, entrypoint
        step 3) asks for, spelled independently of it.  A miscount here
        means the test environment and the production check would
        diverge -- either an
        extra trigger was attached to an un-audited table or the
        canonical list grew without the test fixture catching up.
        """
        actual = db.session.execute(db.text(
            "SELECT count(*) FROM pg_trigger "
            "WHERE tgname LIKE 'audit_%' AND NOT tgisinternal"
        )).scalar()
        assert actual == EXPECTED_TRIGGER_COUNT, (
            f"Expected exactly {EXPECTED_TRIGGER_COUNT} audit_* "
            f"triggers, found {actual}.  See "
            "app/audit_infrastructure.py:AUDITED_TABLES."
        )


# ── User ID Capture via Middleware (WU-2) ─────────────────────────────────


class TestAuditUserIdCapture:
    """Verify that the Flask middleware propagates user_id to audit_log."""

    def test_authenticated_request_captures_user_id(
        self, app, auth_client, seed_user, seed_periods
    ):
        """An authenticated POST that modifies data records user_id in audit_log."""
        projected = db.session.query(Status).filter_by(name="Projected").one()
        expense = db.session.query(TransactionType).filter_by(name="Expense").one()
        auth_client.post(
            "/transactions",
            data={
                "name": "Middleware Test",
                "estimated_amount": "50.00",
                "transaction_type_id": str(expense.id),
                "status_id": str(projected.id),
                "scenario_id": str(seed_user["scenario"].id),
                "pay_period_id": str(seed_periods[0].id),
                "category_id": str(seed_user["categories"]["Rent"].id),
                "account_id": str(seed_user["account"].id),
            },
        )
        rows = _get_audit_rows("transactions", "INSERT")
        # Find the row for our "Middleware Test" transaction.
        middleware_rows = [
            r for r in rows
            if r["new_data"] and r["new_data"].get("name") == "Middleware Test"
        ]
        assert len(middleware_rows) == 1
        assert middleware_rows[0]["user_id"] == seed_user["user"].id

    def test_unauthenticated_request_has_null_user_id(
        self, app, db, seed_user, seed_periods
    ):
        """Direct database operations without middleware produce user_id=NULL."""
        _create_transaction(seed_user, seed_periods)
        rows = _get_audit_rows("transactions", "INSERT")
        txn_row = [
            r for r in rows
            if r["new_data"] and r["new_data"].get("name") == "Test Expense"
        ]
        # Exactly 1 audit row for our specific "Test Expense" INSERT
        assert len(txn_row) == 1
        assert txn_row[-1]["user_id"] is None

    def test_set_local_is_transaction_scoped(self, app, db, seed_user):
        """A transaction-local setting resets after commit -- next txn has no user_id.

        Set the way the application sets it (``app.audit_infrastructure``'s
        ``set_config(..., true)``, ``SET LOCAL``'s function form).  ``SET LOCAL
        ... = :uid`` itself cannot carry a bound parameter under psycopg 3's
        server-side binding (plan step balance:X-dj, ruling R-BAL210).
        """
        # Manually set the session variable, commit, then check it's gone.
        db.session.execute(
            db.text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": str(seed_user["user"].id)},
        )
        db.session.commit()
        # After commit, the setting should be cleared.
        result = db.session.execute(
            db.text("SELECT current_setting('app.current_user_id', true)")
        )
        val = result.scalar()
        # Should be empty string or None after transaction boundary.
        assert val is None or val == ""
