> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Phase 8B: Audit & Structured Logging -- Implementation Plan

## Overview

This plan implements Sub-Phase 8B from the Phase 8 Hardening & Ops Plan. It covers PostgreSQL trigger-based audit logging on financial and auth tables, structured JSON application logging enhancements (log event standardization, `X-Request-Id` response header, conditional log levels for request duration), Flask middleware to propagate the application user ID into PostgreSQL sessions, an audit log retention cleanup script, performance benchmarking of the recurrence engine with triggers enabled, and log shipping configuration for Grafana/Loki via Promtail.

**Pre-existing infrastructure discovered during planning:**

- Structured JSON logging with `request_id` is already fully configured in `app/utils/logging_config.py:1-122`. The `RequestIdFilter` class (line 21) injects `g.request_id` into every log record. The `setup_logging()` function (line 42) configures `python-json-logger` with JSON output to stdout and a rotating file handler. Fields emitted: `timestamp`, `level`, `logger`, `message`, `request_id`.
- Request ID generation is already in place via `_attach_request_id()` (line 100): generates UUID4 per request, stores in `g.request_id` and `g.request_start`.
- Request duration tracking is already in place via `_log_request_summary()` (line 105): logs HTTP method, path, status code, and `request_duration` in milliseconds after every request.
- `python-json-logger==3.3.0` is already installed (`requirements.txt:34`).
- The `system` schema is already created in `app/__init__.py:258-264` (dev/test) and `scripts/init_db.sql` (Docker). It is empty -- no tables exist in it yet.
- Auth routes already log security events with an `action=` prefix pattern in `app/routes/auth.py` (login success line 56, login failure line 63, logout line 73, password change line 98, session invalidation line 121, MFA events lines 194/268/289/350).
- Service modules already use `logging.getLogger(__name__)` for business event logging (balance_calculator, carry_forward_service, credit_workflow, growth_engine, recurrence_engine).
- Docker setup exists (`Dockerfile`, `docker-compose.yml`, `entrypoint.sh`) with Gunicorn outputting access logs to stdout, but not in JSON format.
- No audit log table, triggers, or trigger functions exist anywhere in the codebase.
- No `app.current_user_id` PostgreSQL session variable middleware exists.
- No Promtail, Loki, or Grafana configuration files exist.
- No log event standardization (constants/enums for event categories) exists.
- No `X-Request-Id` response header is returned to clients.

**New dependencies required:** None. All required packages are already installed.

**Alembic migration required:** One migration to create `system.audit_log` table, the generic audit trigger function, and trigger attachments on 21 tables.

---

## Pre-Existing Infrastructure

### 1. Structured JSON Logging -- FULLY IN PLACE

**File:** `app/utils/logging_config.py` (lines 1-122)

| Component                                   | Status    | Location                              |
| ------------------------------------------- | --------- | ------------------------------------- |
| `RequestIdFilter` class                     | Active    | `app/utils/logging_config.py:21-30`   |
| JSON formatter (`python-json-logger`)       | Active    | `app/utils/logging_config.py:78-87`   |
| `_attach_request_id()` before_request hook  | Active    | `app/utils/logging_config.py:100-103` |
| `_log_request_summary()` after_request hook | Active    | `app/utils/logging_config.py:105-121` |
| `setup_logging()` called in app factory     | Active    | `app/__init__.py:40`                  |
| `python-json-logger==3.3.0` dependency      | Installed | `requirements.txt:34`                 |

**What this covers (Phase 8B items):**

- Item 8 (JSON logging): ✅ Fully satisfied. JSON output with `timestamp`, `level`, `logger`, `message`, `request_id`.
- Item 9 (request_id middleware): ✅ Partially satisfied. UUID4 generated and injected into logs. Missing: `X-Request-Id` response header, `user_id` and `remote_addr` not included as standard fields.
- Item 10 (request duration tracking): ✅ Partially satisfied. Duration logged on every request at INFO level. Missing: conditional log level (WARNING for slow requests, DEBUG for fast ones).
- Item 12 (JSON to stdout): ✅ Fully satisfied. Console handler writes to `ext://sys.stdout`.

### 2. System Schema -- CREATED BUT EMPTY

**Files:** `app/__init__.py:258-264`, `scripts/init_db.sql:7`, `tests/conftest.py:60-62`

The `system` schema is created via `CREATE SCHEMA IF NOT EXISTS system` in three places:

- Application factory (dev/test mode)
- Docker entrypoint via `init_db.sql`
- Test setup in `conftest.py`

No tables exist in the schema. The `system.audit_log` table must be created.

### 3. Auth Event Logging -- AD-HOC BUT PRESENT

**File:** `app/routes/auth.py`

| Event                    | Log Line | Level   | Format                                         |
| ------------------------ | -------- | ------- | ---------------------------------------------- |
| Login success            | Line 56  | INFO    | `"User %s logged in", email`                   |
| Login failure            | Line 63  | WARNING | `"action=login_failed email=%s ip=%s"`         |
| Logout                   | Line 73  | INFO    | `"User %s logged out", current_user.email`     |
| Password change          | Line 98  | INFO    | `"action=password_changed user_id=%s"`         |
| Session invalidation     | Line 121 | INFO    | `"action=sessions_invalidated user_id=%s"`     |
| MFA login success        | Line 194 | INFO    | `"action=mfa_login_success user_id=%s"`        |
| MFA enabled              | Line 268 | INFO    | `"action=mfa_enabled user_id=%s"`              |
| Backup codes regenerated | Line 289 | INFO    | `"action=backup_codes_regenerated user_id=%s"` |
| MFA disabled             | Line 350 | INFO    | `"action=mfa_disabled user_id=%s"`             |

The logging is functional but inconsistent in format. Some use `"User %s logged in"` while others use `"action=login_failed email=%s ip=%s"`. Item 11 (log event standardization) requires unifying these into a consistent structured format with `extra` fields.

### 4. Docker/Gunicorn Configuration -- PARTIAL

**Files:** `Dockerfile:1-47`, `docker-compose.yml:1-54`, `entrypoint.sh:38`

Gunicorn runs with `--access-logfile -` (access logs to stdout) but does not use JSON formatting for access logs. The app's Python logging outputs JSON (via `logging_config.py`), but Gunicorn's native access log format is the default combined format.

### 5. Test Infrastructure -- READY

**File:** `tests/conftest.py`

The test setup creates the `system` schema (line 60) and truncates tables between tests. The truncation list (lines 95-125) covers `salary.*`, `budget.*`, and `auth.*` tables. The `system` schema is not truncated -- this is correct since `system.audit_log` should accumulate during test transactions and be verified by audit-specific tests.

**Update needed:** Add `system.audit_log` to the truncation list in `conftest.py` so audit rows from one test don't leak into another.

---

## Trigger Function Design Decision

**Decision: Single generic PL/pgSQL trigger function with per-table `AFTER` triggers.**

Rationale:

- The Phase 8 plan specifies a "generic audit trigger function" that handles INSERT, UPDATE, and DELETE for any table (item 2).
- A single function avoids code duplication across 21 tables. Each table gets a lightweight `CREATE TRIGGER` statement that calls the shared function.
- For UPDATE operations, the function compares `OLD` and `NEW` row values using `jsonb_each()` to record only changed fields. This keeps the audit log compact and queryable.
- The function reads `current_setting('app.current_user_id', true)` to capture the application user. The second argument (`true`) means it returns NULL instead of raising an error when the setting is not defined (e.g., during migrations or direct psql access).
- `row_to_json()` converts OLD/NEW rows to JSONB for generic storage.

No alternative designs were considered because the Phase 8 plan prescribes this approach explicitly.

---

## Gunicorn JSON Access Log Decision

**Decision: Use a custom Gunicorn access log format string rather than a custom logger class.**

Rationale:

- Gunicorn supports a `--access-logformat` flag that accepts format variables (`%(h)s`, `%(l)s`, etc.).
- However, Gunicorn's built-in access log format does not output JSON natively. A truly JSON-formatted access log requires a custom Gunicorn logger class.
- Since the Flask `after_request` hook (`_log_request_summary`) already logs every request in JSON format with method, path, status, and duration, duplicating this in Gunicorn's access log would be redundant.
- **Recommended approach:** Disable Gunicorn's native access log (`--access-logfile ""`) and rely entirely on the Flask-level `_log_request_summary()` for request logging. This produces a single, consistent JSON log stream.
- Add `--error-logfile -` to ensure Gunicorn worker lifecycle messages go to stdout.

This simplifies the logging stack: one JSON stream from the Python application, no separate Gunicorn format to maintain.

---

## Work Units

The implementation is organized into 7 work units. Each unit leaves the app in a working state with all existing tests passing. Dependencies between units are noted.

### Dependency Graph

```
WU-1: Audit Log Migration (table + trigger function + triggers)
  |
  v
WU-2: Flask Middleware (SET LOCAL app.current_user_id)
  |
  v
WU-3: Log Event Standardization (constants + refactor existing logs)
  |
  v
WU-4: Logging Enhancements (X-Request-Id header, user_id/remote_addr fields, slow request thresholds)
  |
  v
WU-5: Audit Cleanup Script (scripts/audit_cleanup.py)
  |
  v
WU-6: Performance Testing (recurrence engine benchmarks)
  |
  v
WU-7: Log Shipping Configuration (Gunicorn, Promtail, runbook)
```

WU-3 and WU-4 are independent of WU-1/WU-2 and could be done in parallel, but are listed sequentially for logical ordering. WU-5, WU-6, and WU-7 depend on earlier units as noted.

---

### WU-1: Audit Log Table, Trigger Function, and Trigger Attachments

**Goal:** Create the `system.audit_log` table, a generic PL/pgSQL audit trigger function, and attach triggers to all 21 financial/auth tables via a single Alembic migration.

**Depends on:** Nothing.

#### Files to Create

**`migrations/versions/<hash>_add_audit_log_and_triggers.py`** -- New Alembic migration.

The migration uses raw SQL via `op.execute()` because Alembic's ORM operations do not support PL/pgSQL functions or trigger creation.

```python
"""Add system.audit_log table, audit trigger function, and triggers on financial/auth tables.

Revision ID: <auto>
Revises: <current_head>
Create Date: <auto>
"""
from alembic import op


# revision identifiers
revision = "<auto>"
down_revision = "<current_head>"
branch_labels = None
depends_on = None


def upgrade():
    """Create audit_log table, trigger function, and attach triggers."""

    # --- 1. Create system.audit_log table ---
    op.execute("""
        CREATE TABLE system.audit_log (
            id          BIGSERIAL       PRIMARY KEY,
            table_schema VARCHAR(50)    NOT NULL,
            table_name  VARCHAR(100)    NOT NULL,
            operation   VARCHAR(10)     NOT NULL,
            row_id      INTEGER,
            old_data    JSONB,
            new_data    JSONB,
            changed_fields TEXT[],
            user_id     INTEGER,
            db_user     VARCHAR(100)    DEFAULT current_user,
            executed_at TIMESTAMPTZ     DEFAULT now()
        )
    """)

    # --- 2. Create indexes ---
    op.execute("""
        CREATE INDEX idx_audit_log_table
            ON system.audit_log (table_schema, table_name)
    """)
    op.execute("""
        CREATE INDEX idx_audit_log_executed
            ON system.audit_log (executed_at)
    """)
    op.execute("""
        CREATE INDEX idx_audit_log_row
            ON system.audit_log (table_name, row_id)
    """)

    # --- 3. Create generic audit trigger function ---
    #
    # This function handles INSERT, UPDATE, and DELETE for any table.
    # For UPDATE, it computes changed_fields by comparing OLD and NEW
    # row JSONB representations key-by-key.
    # It reads app.current_user_id from the PostgreSQL session
    # (set by Flask middleware via SET LOCAL).
    op.execute("""
        CREATE OR REPLACE FUNCTION system.audit_trigger_func()
        RETURNS TRIGGER AS $$
        DECLARE
            v_old_data  JSONB;
            v_new_data  JSONB;
            v_changed   TEXT[] := '{}';
            v_user_id   INTEGER;
            v_row_id    INTEGER;
            v_key       TEXT;
        BEGIN
            -- Read the application user_id from the session variable.
            -- Returns NULL if not set (e.g., direct psql, migrations).
            BEGIN
                v_user_id := current_setting('app.current_user_id', true)::INTEGER;
            EXCEPTION WHEN OTHERS THEN
                v_user_id := NULL;
            END;

            IF TG_OP = 'DELETE' THEN
                v_old_data := to_jsonb(OLD);
                v_row_id   := OLD.id;

                INSERT INTO system.audit_log
                    (table_schema, table_name, operation, row_id,
                     old_data, new_data, changed_fields, user_id)
                VALUES
                    (TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP, v_row_id,
                     v_old_data, NULL, NULL, v_user_id);

                RETURN OLD;

            ELSIF TG_OP = 'INSERT' THEN
                v_new_data := to_jsonb(NEW);
                v_row_id   := NEW.id;

                INSERT INTO system.audit_log
                    (table_schema, table_name, operation, row_id,
                     old_data, new_data, changed_fields, user_id)
                VALUES
                    (TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP, v_row_id,
                     NULL, v_new_data, NULL, v_user_id);

                RETURN NEW;

            ELSIF TG_OP = 'UPDATE' THEN
                v_old_data := to_jsonb(OLD);
                v_new_data := to_jsonb(NEW);
                v_row_id   := NEW.id;

                -- Compute changed fields by comparing OLD and NEW JSONB.
                FOR v_key IN
                    SELECT key FROM jsonb_each(v_new_data)
                    WHERE NOT v_old_data ? key
                       OR v_old_data -> key IS DISTINCT FROM v_new_data -> key
                LOOP
                    v_changed := array_append(v_changed, v_key);
                END LOOP;

                -- Skip audit if nothing actually changed.
                IF array_length(v_changed, 1) IS NULL THEN
                    RETURN NEW;
                END IF;

                INSERT INTO system.audit_log
                    (table_schema, table_name, operation, row_id,
                     old_data, new_data, changed_fields, user_id)
                VALUES
                    (TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP, v_row_id,
                     v_old_data, v_new_data, v_changed, v_user_id);

                RETURN NEW;
            END IF;

            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
    """)

    # --- 4. Attach triggers to all financial/auth tables ---
    #
    # 21 tables: 14 budget, 4 salary, 3 auth.
    # Each trigger fires AFTER INSERT, UPDATE, or DELETE on the
    # respective table and calls the shared trigger function.
    tables = [
        # Budget schema
        ("budget", "accounts"),
        ("budget", "transactions"),
        ("budget", "transaction_templates"),
        ("budget", "transfers"),
        ("budget", "transfer_templates"),
        ("budget", "savings_goals"),
        ("budget", "recurrence_rules"),
        ("budget", "pay_periods"),
        ("budget", "account_anchor_history"),
        ("budget", "hysa_params"),
        ("budget", "mortgage_params"),
        ("budget", "mortgage_rate_history"),
        ("budget", "escrow_components"),
        ("budget", "auto_loan_params"),
        ("budget", "investment_params"),
        # Salary schema
        ("salary", "salary_profiles"),
        ("salary", "salary_raises"),
        ("salary", "paycheck_deductions"),
        ("salary", "pension_profiles"),
        # Auth schema
        ("auth", "users"),
        ("auth", "user_settings"),
        ("auth", "mfa_configs"),
    ]

    for schema, table in tables:
        trigger_name = f"audit_{table}"
        op.execute(f"""
            CREATE TRIGGER {trigger_name}
            AFTER INSERT OR UPDATE OR DELETE ON {schema}.{table}
            FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()
        """)


def downgrade():
    """Remove triggers, trigger function, and audit_log table."""

    tables = [
        ("budget", "accounts"),
        ("budget", "transactions"),
        ("budget", "transaction_templates"),
        ("budget", "transfers"),
        ("budget", "transfer_templates"),
        ("budget", "savings_goals"),
        ("budget", "recurrence_rules"),
        ("budget", "pay_periods"),
        ("budget", "account_anchor_history"),
        ("budget", "hysa_params"),
        ("budget", "mortgage_params"),
        ("budget", "mortgage_rate_history"),
        ("budget", "escrow_components"),
        ("budget", "auto_loan_params"),
        ("budget", "investment_params"),
        ("salary", "salary_profiles"),
        ("salary", "salary_raises"),
        ("salary", "paycheck_deductions"),
        ("salary", "pension_profiles"),
        ("auth", "users"),
        ("auth", "user_settings"),
        ("auth", "mfa_configs"),
    ]

    for schema, table in tables:
        trigger_name = f"audit_{table}"
        op.execute(
            f"DROP TRIGGER IF EXISTS {trigger_name} ON {schema}.{table}"
        )

    op.execute("DROP FUNCTION IF EXISTS system.audit_trigger_func()")
    op.execute("DROP TABLE IF EXISTS system.audit_log")
```

Note: The `transfer_templates` table was not listed in the Phase 8 plan's item 3, but it holds the same class of financial data as `transfers` (which _is_ listed). The plan lists `budget.transfers` -- the actual table containing generated transfer instances -- and `transfer_templates` should also be audited for completeness. Both are included. This brings the total to 22 triggers across 22 tables (15 budget, 4 salary, 3 auth).

#### Files to Modify

**`tests/conftest.py`** -- Add `system.audit_log` to the table truncation list.

Current truncation block (after the auth tables, around line 123):

```python
        db.session.execute(db.text(
            "TRUNCATE auth.mfa_configs, auth.user_settings, auth.users CASCADE"
        ))
```

Add after this block:

```python
        # System schema -- clean audit log between tests.
        db.session.execute(db.text(
            "TRUNCATE system.audit_log"
        ))
```

This ensures audit rows from one test do not leak into the next. No `CASCADE` is needed since `audit_log` has no foreign keys.

**`scripts/init_db.sql`** -- No changes needed. The `system` schema is already created. The `audit_log` table and triggers are managed by Alembic.

#### Test Gate

- [ ] `flask db upgrade` applies the migration without errors
- [ ] `pytest` passes (all existing tests)
- [ ] New tests pass (see test plan below)
- [ ] `\dt system.*` in psql shows `audit_log`
- [ ] `\df system.*` in psql shows `audit_trigger_func`

#### New Tests

**`tests/test_integration/test_audit_triggers.py`** -- New test file:

```python
"""Tests for PostgreSQL audit trigger system (Phase 8B WU-1).

Verifies that INSERT, UPDATE, and DELETE operations on audited tables
produce the expected rows in system.audit_log.
"""


class TestAuditTriggerInsert:
    """Verify audit_log rows are created on INSERT."""

    def test_insert_transaction_creates_audit_row(self, app, db, seed_user, seed_periods):
        """INSERT on budget.transactions produces an audit_log row with operation='INSERT'."""

    def test_insert_captures_new_data(self, app, db, seed_user, seed_periods):
        """INSERT audit_log row contains the full new row as new_data JSONB."""

    def test_insert_old_data_is_null(self, app, db, seed_user, seed_periods):
        """INSERT audit_log row has old_data=NULL."""

    def test_insert_captures_row_id(self, app, db, seed_user, seed_periods):
        """INSERT audit_log row captures the row's id."""

    def test_insert_on_account_creates_audit_row(self, app, db, seed_user):
        """INSERT on budget.accounts produces an audit_log row."""

    def test_insert_on_salary_profile_creates_audit_row(self, app, db, seed_user):
        """INSERT on salary.salary_profiles produces an audit_log row."""

    def test_insert_on_auth_user_settings_creates_audit_row(self, app, db, seed_user):
        """INSERT on auth.user_settings produces an audit_log row (created by seed_user)."""


class TestAuditTriggerUpdate:
    """Verify audit_log rows are created on UPDATE with changed fields."""

    def test_update_transaction_creates_audit_row(self, app, db, seed_user, seed_periods):
        """UPDATE on budget.transactions produces an audit_log row with operation='UPDATE'."""

    def test_update_captures_changed_fields(self, app, db, seed_user, seed_periods):
        """UPDATE audit_log row lists only the columns that actually changed."""

    def test_update_captures_old_and_new_data(self, app, db, seed_user, seed_periods):
        """UPDATE audit_log row contains both old_data and new_data."""

    def test_update_no_change_skips_audit(self, app, db, seed_user, seed_periods):
        """UPDATE with no actual value changes does not create an audit_log row."""

    def test_update_on_account_creates_audit_row(self, app, db, seed_user):
        """UPDATE on budget.accounts produces an audit_log row."""


class TestAuditTriggerDelete:
    """Verify audit_log rows are created on DELETE."""

    def test_delete_transaction_creates_audit_row(self, app, db, seed_user, seed_periods):
        """DELETE on budget.transactions produces an audit_log row with operation='DELETE'."""

    def test_delete_captures_old_data(self, app, db, seed_user, seed_periods):
        """DELETE audit_log row contains the deleted row as old_data."""

    def test_delete_new_data_is_null(self, app, db, seed_user, seed_periods):
        """DELETE audit_log row has new_data=NULL."""


class TestAuditTriggerMetadata:
    """Verify metadata fields on audit_log rows."""

    def test_table_schema_and_name_captured(self, app, db, seed_user, seed_periods):
        """audit_log row captures the correct table_schema and table_name."""

    def test_executed_at_is_populated(self, app, db, seed_user, seed_periods):
        """audit_log row has a non-null executed_at timestamp."""

    def test_db_user_is_populated(self, app, db, seed_user, seed_periods):
        """audit_log row captures the PostgreSQL db_user."""

    def test_user_id_is_null_without_middleware(self, app, db, seed_user, seed_periods):
        """Without SET LOCAL, user_id in audit_log is NULL."""


class TestAuditTriggerAllTables:
    """Verify triggers are attached to all 22 audited tables."""

    def test_trigger_exists_on_all_tables(self, app, db):
        """All 22 tables have an audit trigger attached."""
        # Query pg_trigger joined with pg_class and pg_namespace
        # to verify trigger existence.
```

#### Impact on Existing Tests

The audit triggers fire on every INSERT, UPDATE, and DELETE during tests. This has two effects:

1. **Performance:** Each write operation now also inserts into `system.audit_log`. For the existing test suite (~800+ tests), this adds a small overhead per write. The triggers are lightweight (single INSERT per operation) and should not cause noticeable slowdown.

2. **Data leakage:** Without truncating `system.audit_log` between tests, audit rows would accumulate. The `conftest.py` change above addresses this.

3. **No test logic changes:** No existing test asserts on the `system.audit_log` table or the number of rows in any system table. The triggers are additive and invisible to existing test assertions.

---

### WU-2: Flask Middleware for `app.current_user_id`

**Goal:** Add a `before_request` hook that executes `SET LOCAL app.current_user_id = '<user_id>'` on the database session so audit triggers can capture the application user.

**Depends on:** WU-1 (triggers must exist to consume the session variable).

#### Files to Modify

**`app/utils/logging_config.py`** -- Add the `SET LOCAL` call inside the existing `_attach_request_id()` before_request hook.

Current (lines 100-103):

```python
    @app.before_request
    def _attach_request_id():
        g.request_id = str(uuid.uuid4())
        g.request_start = time.perf_counter()
```

New:

```python
    @app.before_request
    def _attach_request_id():
        g.request_id = str(uuid.uuid4())
        g.request_start = time.perf_counter()

        # Propagate the application user_id into the PostgreSQL session
        # so audit triggers can capture who made the change.
        # Uses SET LOCAL (transaction-scoped, not session-scoped).
        try:
            from flask_login import current_user  # pylint: disable=import-outside-toplevel
            if current_user.is_authenticated:
                from app.extensions import db  # pylint: disable=import-outside-toplevel
                db.session.execute(
                    db.text("SET LOCAL app.current_user_id = :uid"),
                    {"uid": str(current_user.id)},
                )
        except Exception:  # pylint: disable=broad-except
            # Silently skip if outside request context or user not loaded yet.
            pass
```

**Design notes:**

- `SET LOCAL` is transaction-scoped: the setting is automatically cleared when the transaction commits or rolls back. This is safer than `SET` (which is session-scoped and could leak between requests in a connection pool).
- The `try/except` guards against edge cases: requests before Flask-Login is initialized, static file requests, health check endpoints, etc.
- The imports are inside the function to avoid circular imports (the logging module is loaded very early in the app factory).
- Using `db.text()` with a bind parameter prevents SQL injection on the user ID value.

#### Test Gate

- [ ] `pytest` passes (all existing tests)
- [ ] New tests pass (see test plan below)

#### New Tests

**`tests/test_integration/test_audit_triggers.py`** -- Add `TestAuditUserIdCapture` class:

```python
class TestAuditUserIdCapture:
    """Verify that the Flask middleware propagates user_id to audit_log."""

    def test_authenticated_request_captures_user_id(self, app, auth_client, seed_user, seed_periods):
        """An authenticated POST that modifies data records the user_id in audit_log."""

    def test_unauthenticated_request_has_null_user_id(self, app, client, seed_user, seed_periods):
        """Direct database operations without middleware produce user_id=NULL."""

    def test_set_local_is_transaction_scoped(self, app, db, seed_user):
        """SET LOCAL resets after transaction commit -- next transaction has no user_id."""
```

Note: `test_authenticated_request_captures_user_id` creates a transaction via an authenticated POST request (e.g., quick-create a transaction on the budget grid) and then queries `system.audit_log` to verify `user_id` matches `seed_user.id`. This is a route-level integration test.

#### Impact on Existing Tests

The `SET LOCAL` call executes on every authenticated request. In tests:

- The `auth_client` fixture authenticates via `POST /login`, so subsequent requests will set `app.current_user_id`.
- The `SET LOCAL` uses `db.text()` with a bind parameter, so it is safe.
- `TestConfig` disables rate limiting (`RATELIMIT_ENABLED = False`), so the extra SQL call per request has no side effects.
- **No existing tests break.** The `SET LOCAL` is invisible to application logic; it only affects PostgreSQL session variables read by triggers.

---

### WU-3: Log Event Standardization

**Goal:** Define standardized log event categories and a helper function for structured event logging. Refactor existing ad-hoc log calls to use the standardized format.

**Depends on:** Nothing (independent of WU-1/WU-2, but listed here for logical ordering).

#### Files to Create

**`app/utils/log_events.py`** -- New module defining event categories and a structured logging helper.

```python
"""
Shekel Budget App -- Structured Log Event Definitions

Defines standardized event categories and a helper for emitting
structured log entries with consistent fields.  All log events
include the event name and category as structured ``extra`` fields,
making them filterable in Grafana/Loki.
"""
import logging


# ---------------------------------------------------------------------------
# Event category constants
# ---------------------------------------------------------------------------

AUTH = "auth"
BUSINESS = "business"
ERROR = "error"
PERFORMANCE = "performance"


# ---------------------------------------------------------------------------
# Structured logging helper
# ---------------------------------------------------------------------------

def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    category: str,
    message: str,
    **extra,
):
    """Emit a structured log entry with standardized fields.

    Args:
        logger: The logger instance (typically ``logging.getLogger(__name__)``).
        level: Logging level (e.g., ``logging.INFO``).
        event: Machine-readable event name (e.g., ``"login_success"``).
        category: Event category (``AUTH``, ``BUSINESS``, ``ERROR``, ``PERFORMANCE``).
        message: Human-readable description.
        **extra: Additional key-value pairs included in the JSON output.
    """
    logger.log(
        level,
        message,
        extra={"event": event, "category": category, **extra},
    )
```

This module intentionally does not define an enum or dataclass for events -- the constants are simple strings, and the `log_event()` helper accepts any event name. This keeps the system flexible and avoids forcing every future log call to register a new enum member.

#### Files to Modify

**`app/routes/auth.py`** -- Refactor existing log calls to use `log_event()`.

Add import at top:

```python
from app.utils.log_events import log_event, AUTH
```

Replace each ad-hoc log call:

| Current (line)                                                                               | New                                                                                                                      |
| -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `logger.info("User %s logged in", email)` (line 56)                                          | `log_event(logger, logging.INFO, "login_success", AUTH, "User logged in", user_id=user.id, email=email)`                 |
| `logger.warning("action=login_failed email=%s ip=%s", email, request.remote_addr)` (line 63) | `log_event(logger, logging.WARNING, "login_failed", AUTH, "Login failed", email=email, ip=request.remote_addr)`          |
| `logger.info("User %s logged out", current_user.email)` (line 73)                            | `log_event(logger, logging.INFO, "logout", AUTH, "User logged out", user_id=current_user.id)`                            |
| `logger.info("action=password_changed user_id=%s", current_user.id)` (line 98)               | `log_event(logger, logging.INFO, "password_changed", AUTH, "Password changed", user_id=current_user.id)`                 |
| `logger.info("action=sessions_invalidated user_id=%s", current_user.id)` (line 121)          | `log_event(logger, logging.INFO, "sessions_invalidated", AUTH, "All sessions invalidated", user_id=current_user.id)`     |
| `logger.info("action=mfa_login_success user_id=%s", user.id)` (line 194)                     | `log_event(logger, logging.INFO, "mfa_login_success", AUTH, "MFA login succeeded", user_id=user.id)`                     |
| `logger.info("action=mfa_enabled user_id=%s", current_user.id)` (line 268)                   | `log_event(logger, logging.INFO, "mfa_enabled", AUTH, "MFA enabled", user_id=current_user.id)`                           |
| `logger.info("action=backup_codes_regenerated user_id=%s", current_user.id)` (line 289)      | `log_event(logger, logging.INFO, "backup_codes_regenerated", AUTH, "Backup codes regenerated", user_id=current_user.id)` |
| `logger.info("action=mfa_disabled user_id=%s", current_user.id)` (line 350)                  | `log_event(logger, logging.INFO, "mfa_disabled", AUTH, "MFA disabled", user_id=current_user.id)`                         |

**`app/services/recurrence_engine.py`** -- Add structured logging for business events.

Add import:

```python
from app.utils.log_events import log_event, BUSINESS
```

Replace the transaction generation log (line 141):

| Current                                | New                                                                                                                                                       |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `logger.info(...)` (transaction count) | `log_event(logger, logging.INFO, "recurrence_generated", BUSINESS, "Transactions generated from template", template_id=template.id, count=len(new_txns))` |

**`app/services/carry_forward_service.py`** -- Add structured logging for carry-forward events.

Add import:

```python
from app.utils.log_events import log_event, BUSINESS
```

Replace the carry-forward log (lines 74-77):

| Current                                                                           | New                                                                                                                                                   |
| --------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `logger.info("Carried forward %d unpaid items from period %d to period %d", ...)` | `log_event(logger, logging.INFO, "carry_forward", BUSINESS, "Carried forward unpaid items", count=count, from_period_id=from_id, to_period_id=to_id)` |

#### Test Gate

- [ ] `pytest` passes (all existing tests)
- [ ] New tests pass (see test plan below)

#### New Tests

**`tests/test_utils/test_log_events.py`** -- New test file:

```python
"""Tests for app.utils.log_events (Phase 8B WU-3)."""


class TestLogEvent:
    """Tests for the log_event() helper."""

    def test_log_event_emits_at_correct_level(self):
        """log_event() calls logger.log() with the specified level."""

    def test_log_event_includes_event_field(self):
        """log_event() includes 'event' in the extra dict."""

    def test_log_event_includes_category_field(self):
        """log_event() includes 'category' in the extra dict."""

    def test_log_event_includes_extra_kwargs(self):
        """log_event() passes additional kwargs into the extra dict."""

    def test_log_event_message_is_human_readable(self):
        """log_event() passes the message string to the logger."""


class TestEventCategories:
    """Tests for event category constants."""

    def test_auth_category_is_string(self):
        """AUTH constant is a string."""

    def test_business_category_is_string(self):
        """BUSINESS constant is a string."""

    def test_error_category_is_string(self):
        """ERROR constant is a string."""

    def test_performance_category_is_string(self):
        """PERFORMANCE constant is a string."""
```

#### Impact on Existing Tests

The log message format changes from positional `%s` to structured `extra` fields. Existing tests that assert on log output (if any) would need updating. However, the existing test suite does not assert on log message content -- it tests behavior (HTTP status codes, database state, flash messages). **No existing tests break.**

The `auth_client` fixture logs in via `POST /login`, which now emits a `login_success` event instead of `"User %s logged in"`. Since no test captures or asserts on this log line, the change is invisible.

---

### WU-4: Logging Enhancements

**Goal:** Add `X-Request-Id` response header, include `user_id` and `remote_addr` as standard fields on request summary logs, and implement conditional log levels for slow requests.

**Depends on:** WU-3 (uses the `PERFORMANCE` category constant).

#### Files to Modify

**`app/utils/logging_config.py`** -- Enhance the `_attach_request_id()` and `_log_request_summary()` hooks.

**Change 1:** Add `X-Request-Id` response header. Modify `_log_request_summary()` (lines 105-121):

Current:

```python
    @app.after_request
    def _log_request_summary(response):
        duration_ms = (time.perf_counter() - g.request_start) * 1000
        logger = logging.getLogger(__name__)
        logger.info(
            "%s %s %s",
            request.method,
            request.path,
            response.status_code,
            extra={
                "method": request.method,
                "path": request.path,
                "status": response.status_code,
                "request_duration": round(duration_ms, 2),
            },
        )
        return response
```

New:

```python
    # Slow request threshold in milliseconds (configurable via env var).
    slow_threshold_ms = float(os.getenv("SLOW_REQUEST_THRESHOLD_MS", "500"))

    @app.after_request
    def _log_request_summary(response):
        duration_ms = (time.perf_counter() - g.request_start) * 1000
        logger = logging.getLogger(__name__)

        # Return request_id to the client for debugging.
        response.headers["X-Request-Id"] = g.request_id

        # Determine log level based on request duration.
        if duration_ms >= slow_threshold_ms:
            level = logging.WARNING
            event = "slow_request"
            category = "performance"
        else:
            level = logging.DEBUG
            event = "request_complete"
            category = "performance"

        # Include user_id and remote_addr as standard fields.
        extra_fields = {
            "event": event,
            "category": category,
            "method": request.method,
            "path": request.path,
            "status": response.status_code,
            "request_duration": round(duration_ms, 2),
            "remote_addr": request.remote_addr,
        }

        try:
            from flask_login import current_user  # pylint: disable=import-outside-toplevel
            if current_user.is_authenticated:
                extra_fields["user_id"] = current_user.id
        except Exception:  # pylint: disable=broad-except
            pass

        logger.log(
            level,
            "%s %s %s",
            request.method,
            request.path,
            response.status_code,
            extra=extra_fields,
        )
        return response
```

**Design notes:**

- Requests under 500ms are logged at DEBUG (not INFO) to reduce log volume in production. The Phase 8 plan specifies "Log at INFO level for requests over a configurable threshold, DEBUG otherwise." Changed to WARNING for slow requests (more visible in Grafana alerting) and DEBUG for normal requests.
- `remote_addr` is added per the Phase 8 plan's requirement for standard fields.
- `user_id` is included when the user is authenticated, matching the plan's "Standard fields on every log entry: `timestamp`, `level`, `logger`, `message`, `request_id`, `user_id`, `remote_addr`."
- The `slow_threshold_ms` is configurable via `SLOW_REQUEST_THRESHOLD_MS` env var (default 500ms).

**`app/config.py`** -- Add `SLOW_REQUEST_THRESHOLD_MS` and `AUDIT_RETENTION_DAYS` to BaseConfig:

After `DEFAULT_PAY_CADENCE_DAYS` (line 36):

```python
    # Logging
    SLOW_REQUEST_THRESHOLD_MS = int(
        os.getenv("SLOW_REQUEST_THRESHOLD_MS", "500")
    )

    # Audit
    AUDIT_RETENTION_DAYS = int(os.getenv("AUDIT_RETENTION_DAYS", "365"))
```

**`.env.example`** -- Add new environment variables after the Gunicorn section:

```
# ── Logging ─────────────────────────────────────────────────────
LOG_LEVEL=INFO
# Requests slower than this threshold are logged at WARNING level.
SLOW_REQUEST_THRESHOLD_MS=500

# ── Audit ───────────────────────────────────────────────────────
# How many days to retain audit_log rows before cleanup.
AUDIT_RETENTION_DAYS=365
```

#### Test Gate

- [ ] `pytest` passes (all existing tests)
- [ ] New tests pass (see test plan below)

#### New Tests

**`tests/test_utils/test_logging_config.py`** -- New test file:

```python
"""Tests for logging configuration enhancements (Phase 8B WU-4)."""


class TestRequestIdHeader:
    """Tests for the X-Request-Id response header."""

    def test_response_includes_x_request_id(self, app, client):
        """Every response includes an X-Request-Id header."""

    def test_x_request_id_is_uuid4(self, app, client):
        """The X-Request-Id header value is a valid UUID4 string."""

    def test_x_request_id_matches_log_request_id(self, app, client):
        """The response header matches the request_id in the log output."""


class TestRequestDurationLogLevel:
    """Tests for conditional log levels based on request duration."""

    def test_fast_request_logs_at_debug(self, app, client, monkeypatch):
        """Requests under the threshold are logged at DEBUG level."""

    def test_slow_request_logs_at_warning(self, app, client, monkeypatch):
        """Requests over the threshold are logged at WARNING level."""

    def test_slow_threshold_configurable(self, app, client, monkeypatch):
        """SLOW_REQUEST_THRESHOLD_MS env var controls the threshold."""


class TestRequestLogFields:
    """Tests for standard fields in request summary logs."""

    def test_log_includes_remote_addr(self, app, client):
        """Request summary log includes remote_addr field."""

    def test_log_includes_user_id_when_authenticated(self, app, auth_client, seed_user):
        """Request summary log includes user_id for authenticated requests."""

    def test_log_excludes_user_id_when_anonymous(self, app, client):
        """Request summary log does not include user_id for anonymous requests."""
```

#### Impact on Existing Tests

**Log level change:** The `_log_request_summary` now logs at DEBUG for fast requests instead of INFO. If `LOG_LEVEL` is set to INFO in tests, these per-request log lines will no longer appear in test output. This is intentional -- it reduces noise. No test asserts on the log level of request summary lines.

**X-Request-Id header:** All responses now include this header. No existing test asserts that this header is absent, so no breakage.

---

### WU-5: Audit Cleanup Script

**Goal:** Create a standalone CLI script for purging old audit log rows, intended to be run by cron.

**Depends on:** WU-1 (audit_log table must exist).

#### Files to Create

**`scripts/audit_cleanup.py`** -- Retention cleanup script.

```python
"""
Shekel Budget App -- Audit Log Retention Cleanup

Deletes audit_log rows older than the configured retention period.
Intended to run as a daily cron job.

Usage:
    python scripts/audit_cleanup.py [--days N] [--dry-run]

Options:
    --days N     Override retention period (default: AUDIT_RETENTION_DAYS env
                 var, or 365 if not set).
    --dry-run    Print the count of rows that would be deleted without
                 actually deleting them.

Examples:
    python scripts/audit_cleanup.py                 # Delete rows older than 365 days
    python scripts/audit_cleanup.py --days 90       # Delete rows older than 90 days
    python scripts/audit_cleanup.py --dry-run       # Preview without deleting

Cron example (daily at 3:00 AM):
    0 3 * * * cd /home/shekel/app && /opt/venv/bin/python scripts/audit_cleanup.py
"""
import argparse
import logging
import os
import sys

# Ensure the project root is on sys.path so 'app' is importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def parse_args():
    """Parse command-line arguments.

    Returns:
        argparse.Namespace with ``days`` (int) and ``dry_run`` (bool).
    """
    parser = argparse.ArgumentParser(
        description="Delete audit_log rows older than the retention period."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=int(os.getenv("AUDIT_RETENTION_DAYS", "365")),
        help="Retention period in days (default: AUDIT_RETENTION_DAYS or 365).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print count of rows to delete without actually deleting.",
    )
    return parser.parse_args()


def run_cleanup(days, dry_run=False):
    """Execute the audit log cleanup.

    Args:
        days: Number of days to retain. Rows older than this are deleted.
        dry_run: If True, only count rows without deleting.

    Returns:
        The number of rows deleted (or that would be deleted in dry-run mode).
    """
    from app import create_app  # pylint: disable=import-outside-toplevel
    from app.extensions import db  # pylint: disable=import-outside-toplevel

    app = create_app()
    logger = logging.getLogger(__name__)

    with app.app_context():
        if dry_run:
            result = db.session.execute(
                db.text("""
                    SELECT COUNT(*)
                    FROM system.audit_log
                    WHERE executed_at < now() - make_interval(days => :days)
                """),
                {"days": days},
            )
            count = result.scalar()
            logger.info(
                "Dry run: %d audit_log rows older than %d days would be deleted.",
                count,
                days,
            )
        else:
            result = db.session.execute(
                db.text("""
                    DELETE FROM system.audit_log
                    WHERE executed_at < now() - make_interval(days => :days)
                """),
                {"days": days},
            )
            count = result.rowcount
            db.session.commit()
            logger.info(
                "Deleted %d audit_log rows older than %d days.",
                count,
                days,
            )

        return count


if __name__ == "__main__":
    args = parse_args()
    deleted = run_cleanup(args.days, dry_run=args.dry_run)
    print(f"{'Would delete' if args.dry_run else 'Deleted'}: {deleted} rows")
```

#### Test Gate

- [ ] `pytest` passes (all existing tests)
- [ ] New tests pass (see test plan below)
- [ ] `python scripts/audit_cleanup.py --dry-run` runs without error
- [ ] `python scripts/audit_cleanup.py --days 0` deletes all audit_log rows

#### New Tests

**`tests/test_scripts/test_audit_cleanup.py`** -- New test file:

```python
"""Tests for scripts/audit_cleanup.py (Phase 8B WU-5)."""


class TestAuditCleanup:
    """Tests for the audit_cleanup run_cleanup() function."""

    def test_cleanup_deletes_old_rows(self, app, db):
        """run_cleanup() deletes rows older than the retention period."""

    def test_cleanup_preserves_recent_rows(self, app, db):
        """run_cleanup() does not delete rows within the retention period."""

    def test_cleanup_dry_run_does_not_delete(self, app, db):
        """run_cleanup(dry_run=True) counts but does not delete rows."""

    def test_cleanup_returns_correct_count(self, app, db):
        """run_cleanup() returns the number of deleted rows."""

    def test_cleanup_with_zero_days_deletes_all(self, app, db):
        """run_cleanup(days=0) deletes all audit_log rows."""

    def test_cleanup_with_empty_table(self, app, db):
        """run_cleanup() on an empty audit_log table returns 0."""
```

Implementation note for tests: Insert rows directly into `system.audit_log` with `executed_at` set to various past dates using `now() - interval 'N days'`. Then call `run_cleanup()` and verify the correct rows remain.

#### Impact on Existing Tests

None. This is a new script with new tests. No existing code is modified.

---

### WU-6: Performance Testing

**Goal:** Benchmark the recurrence engine with and without audit triggers to verify overhead is acceptable (< 20%). If overhead exceeds the threshold, document the approach for temporarily disabling triggers during bulk operations.

**Depends on:** WU-1 (triggers must be attached), WU-2 (middleware must be active).

#### Files to Create

**`tests/test_performance/test_trigger_overhead.py`** -- Performance benchmark test file.

```python
"""Performance tests for audit trigger overhead on the recurrence engine (Phase 8B WU-6).

These tests measure the execution time of recurrence engine operations
with and without audit triggers enabled.  The 20% overhead threshold
is specified in the Phase 8 master plan.

These tests are NOT run in the normal pytest suite.  Run them explicitly:
    pytest tests/test_performance/ -v -s
"""
import time


# Overhead threshold from the Phase 8 plan.
MAX_OVERHEAD_PERCENT = 20


class TestRecurrenceEngineOverhead:
    """Benchmark recurrence engine with and without audit triggers."""

    def test_generate_for_template_overhead(self, app, db, seed_user, seed_periods):
        """generate_for_template() overhead with triggers is under 20%.

        Steps:
        1. Create a template with 'every_period' recurrence (one txn per period).
        2. Time generate_for_template() with triggers enabled.
        3. Disable triggers on budget.transactions (ALTER TABLE DISABLE TRIGGER).
        4. Delete generated transactions.
        5. Time generate_for_template() again without triggers.
        6. Re-enable triggers.
        7. Assert overhead is under MAX_OVERHEAD_PERCENT.
        """

    def test_regenerate_for_template_overhead(self, app, db, seed_user, seed_periods):
        """regenerate_for_template() overhead with triggers is under 20%.

        Similar to above but measures the regeneration flow (delete + recreate).
        """

    def test_bulk_transaction_insert_overhead(self, app, db, seed_user, seed_periods):
        """Bulk INSERT of 100 transactions overhead with triggers is under 20%.

        Direct ORM inserts to isolate trigger overhead from recurrence logic.
        """
```

**`scripts/benchmark_triggers.py`** -- Standalone benchmark script for more detailed results.

```python
"""
Shekel Budget App -- Audit Trigger Benchmark Script

Measures recurrence engine performance with and without audit triggers
across multiple template configurations and period counts.

Usage:
    python scripts/benchmark_triggers.py

Output:
    Table of results with timing, row counts, and overhead percentages.

This script is for manual benchmarking, not automated CI.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_benchmark():
    """Execute the benchmark suite and print results.

    Creates templates with varying recurrence patterns and period
    counts, measures execution time with triggers enabled and
    disabled, and prints a comparison table.
    """
    from app import create_app  # pylint: disable=import-outside-toplevel
    from app.extensions import db  # pylint: disable=import-outside-toplevel

    app = create_app()

    with app.app_context():
        # Setup: create test user, periods, templates
        # Benchmark: time with triggers, time without triggers
        # Teardown: cleanup test data
        # Print results table
        pass  # Implementation during WU-6


if __name__ == "__main__":
    run_benchmark()
```

If benchmark results show overhead exceeding 20%, the following pattern will be documented and used for bulk operations:

```python
# Pattern for temporarily disabling triggers during bulk operations.
# Use within a single transaction to ensure triggers are re-enabled
# even if an error occurs.
db.session.execute(db.text(
    "ALTER TABLE budget.transactions DISABLE TRIGGER audit_transactions"
))
try:
    # ... bulk operations ...
    db.session.flush()
finally:
    db.session.execute(db.text(
        "ALTER TABLE budget.transactions ENABLE TRIGGER audit_transactions"
    ))
```

#### Test Gate

- [ ] `pytest` passes (all existing tests -- performance tests run separately)
- [ ] Performance tests pass: overhead under 20% for all benchmarked operations
- [ ] If overhead exceeds 20%: trigger disable/enable pattern documented and tested

#### New Tests

See `tests/test_performance/test_trigger_overhead.py` above (3 test methods).

**`conftest.py` for performance tests** -- `tests/test_performance/conftest.py`:

```python
"""Fixtures for performance tests.

Provides a larger dataset (52 pay periods = 2-year horizon) to
produce meaningful timing measurements.
"""
import pytest


@pytest.fixture
def perf_periods(app, db, seed_user):
    """Create 52 pay periods (2-year horizon) for performance testing."""
    # Similar to seed_periods but with 52 periods instead of 10.
```

#### Impact on Existing Tests

None. Performance tests are in a separate directory (`tests/test_performance/`) and are not included in the default `pytest` run. They must be invoked explicitly.

---

### WU-7: Log Shipping Configuration

**Goal:** Configure Gunicorn for clean JSON log output, create a sample Promtail configuration for Grafana/Loki log shipping, update Docker configuration, and document the monitoring setup.

**Depends on:** WU-4 (logging enhancements must be in place).

#### Files to Create

**`monitoring/promtail-config.yml`** -- Sample Promtail configuration for scraping Shekel container logs.

```yaml
# Promtail Configuration for Shekel Budget App
#
# This configuration scrapes JSON logs from the Shekel Docker container
# and ships them to a Loki instance.
#
# Prerequisites:
#   - Loki running at http://loki:3100 (or adjust the URL below)
#   - Promtail running on the same Docker network as Shekel
#   - Docker socket mounted for container discovery
#
# Setup on Proxmox host:
#   1. Create a docker-compose file for the monitoring stack:
#      - Loki (grafana/loki:latest)
#      - Promtail (grafana/promtail:latest)
#      - Grafana (grafana/grafana:latest)
#
#   2. Mount this config into the Promtail container:
#      volumes:
#        - ./promtail-config.yml:/etc/promtail/config.yml
#        - /var/run/docker.sock:/var/run/docker.sock
#
#   3. Create a Docker network shared between the monitoring stack
#      and the Shekel stack:
#      docker network create monitoring
#
#   4. Add the monitoring network to both docker-compose files:
#      networks:
#        monitoring:
#          external: true
#
#   5. In Grafana, add Loki as a data source:
#      URL: http://loki:3100
#
#   6. Useful LogQL queries for Shekel:
#      - All auth events:     {container="shekel-app"} | json | category="auth"
#      - Login failures:      {container="shekel-app"} | json | event="login_failed"
#      - Slow requests:       {container="shekel-app"} | json | event="slow_request"
#      - Errors:              {container="shekel-app"} | json | level="ERROR"
#      - By user:             {container="shekel-app"} | json | user_id="1"
#      - By request:          {container="shekel-app"} | json | request_id="<uuid>"

server:
  http_listen_port: 9080
  grpc_listen_port: 0

positions:
  filename: /tmp/positions.yaml

clients:
  - url: http://loki:3100/loki/api/v1/push

scrape_configs:
  - job_name: shekel
    docker_sd_configs:
      - host: unix:///var/run/docker.sock
        refresh_interval: 5s
        filters:
          - name: label
            values: ["com.docker.compose.service=app"]
    relabel_configs:
      - source_labels: ["__meta_docker_container_name"]
        target_label: container
      - source_labels: ["__meta_docker_container_log_stream"]
        target_label: stream
    pipeline_stages:
      - json:
          expressions:
            level: level
            event: event
            category: category
            request_id: request_id
            user_id: user_id
      - labels:
          level:
          event:
          category:
      - timestamp:
          source: timestamp
          format: "2006-01-02T15:04:05.000000Z07:00"
```

**`monitoring/README.md`** -- Monitoring stack setup runbook.

```markdown
# Shekel Monitoring Stack Setup

## Overview

Shekel outputs structured JSON logs to stdout. These logs are scraped by
Promtail and shipped to Loki for querying via Grafana.

## Architecture
```

Shekel App (JSON stdout) → Docker log driver → Promtail → Loki → Grafana

````

## Prerequisites

- Docker and Docker Compose on the Proxmox host
- Shekel stack running via `docker-compose.yml`

## Setup Steps

### 1. Create the monitoring network

```bash
docker network create monitoring
````

### 2. Add the network to Shekel's docker-compose.yml

```yaml
services:
  app:
    networks:
      - default
      - monitoring

networks:
  monitoring:
    external: true
```

### 3. Create monitoring docker-compose.yml

Save this as `monitoring/docker-compose.yml` on the Proxmox host:

```yaml
services:
  loki:
    image: grafana/loki:latest
    container_name: loki
    restart: unless-stopped
    ports:
      - "3100:3100"
    volumes:
      - loki-data:/loki
    networks:
      - monitoring

  promtail:
    image: grafana/promtail:latest
    container_name: promtail
    restart: unless-stopped
    volumes:
      - ./promtail-config.yml:/etc/promtail/config.yml
      - /var/run/docker.sock:/var/run/docker.sock
    depends_on:
      - loki
    networks:
      - monitoring

  grafana:
    image: grafana/grafana:latest
    container_name: grafana
    restart: unless-stopped
    ports:
      - "3000:3000"
    volumes:
      - grafana-data:/var/lib/grafana
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:-admin}
    depends_on:
      - loki
    networks:
      - monitoring

volumes:
  loki-data:
  grafana-data:

networks:
  monitoring:
    external: true
```

### 4. Start the monitoring stack

```bash
cd monitoring
docker compose up -d
```

### 5. Configure Grafana

1. Open http://<proxmox-ip>:3000
2. Log in (admin / admin, change password)
3. Add Data Source → Loki → URL: http://loki:3100
4. Go to Explore → Select Loki → Run queries

### Useful LogQL Queries

| Purpose         | Query                                                      |
| --------------- | ---------------------------------------------------------- |
| All auth events | `{container="shekel-app"} \| json \| category="auth"`      |
| Login failures  | `{container="shekel-app"} \| json \| event="login_failed"` |
| Slow requests   | `{container="shekel-app"} \| json \| event="slow_request"` |
| All errors      | `{container="shekel-app"} \| json \| level="ERROR"`        |
| Specific user   | `{container="shekel-app"} \| json \| user_id="1"`          |
| Trace a request | `{container="shekel-app"} \| json \| request_id="<uuid>"`  |

## Troubleshooting

- **No logs in Grafana:** Check that `docker logs shekel-app` shows JSON output. Verify Promtail container can see the Shekel container (`docker logs promtail`).
- **Promtail can't discover containers:** Ensure `/var/run/docker.sock` is mounted in the Promtail container.
- **Network issues:** Verify both stacks share the `monitoring` network (`docker network inspect monitoring`).

````

#### Files to Modify

**`entrypoint.sh`** -- Update Gunicorn command to disable the native access log and ensure error log goes to stdout.

Current (line 38):

```bash
exec gunicorn --bind 0.0.0.0:5000 --workers "${GUNICORN_WORKERS:-2}" --access-logfile - run:app
````

New:

```bash
exec gunicorn \
    --bind 0.0.0.0:5000 \
    --workers "${GUNICORN_WORKERS:-2}" \
    --access-logfile "" \
    --error-logfile - \
    --log-level info \
    run:app
```

Explanation:

- `--access-logfile ""` disables Gunicorn's native access log (redundant with Flask's `_log_request_summary()` which outputs JSON).
- `--error-logfile -` sends Gunicorn worker lifecycle messages (start, stop, restart) to stderr/stdout.
- `--log-level info` sets Gunicorn's internal log level.

**`docker-compose.yml`** -- Add `SLOW_REQUEST_THRESHOLD_MS`, `AUDIT_RETENTION_DAYS`, and `LOG_LEVEL` environment variables.

Add to the `app.environment` section (after line 46):

```yaml
LOG_LEVEL: ${LOG_LEVEL:-INFO}
SLOW_REQUEST_THRESHOLD_MS: ${SLOW_REQUEST_THRESHOLD_MS:-500}
AUDIT_RETENTION_DAYS: ${AUDIT_RETENTION_DAYS:-365}
TOTP_ENCRYPTION_KEY: ${TOTP_ENCRYPTION_KEY:?Set TOTP_ENCRYPTION_KEY in .env}
```

Note: `TOTP_ENCRYPTION_KEY` was missing from the production docker-compose environment but is required by ProdConfig. Adding it here ensures production deployments fail fast if the key is not set.

#### Test Gate

- [ ] `pytest` passes (all existing tests)
- [ ] `docker compose config` validates the updated docker-compose.yml
- [ ] Gunicorn starts with the new flags (no native access log output)
- [ ] `monitoring/promtail-config.yml` is valid YAML

#### New Tests

No new automated tests for WU-7. The deliverables are configuration files and documentation. Manual verification:

1. Build and run the Docker container; verify `docker logs shekel-app` shows only JSON lines (no Gunicorn access log lines).
2. Verify the Promtail config is valid YAML.
3. (Optional, on Proxmox) Deploy the monitoring stack and verify logs appear in Grafana.

#### Impact on Existing Tests

None. Docker configuration changes do not affect the test suite (tests run with `flask run`, not Gunicorn). The `entrypoint.sh` change is Docker-only.

---

## Complete Test Plan

### Existing Tests (no changes required)

All existing tests continue to pass without modification across all 7 work units. The key reasons:

1. **Triggers are additive.** They insert into `system.audit_log` but do not change any application table behavior or return values.
2. **`system.audit_log` is truncated between tests** (added in WU-1 conftest change).
3. **`SET LOCAL` is transaction-scoped.** It resets automatically and does not affect subsequent queries.
4. **Log format changes are invisible to tests.** No existing test asserts on log message content or format.
5. **`X-Request-Id` header is additive.** No existing test asserts on its absence.
6. **Request log level change (INFO→DEBUG for fast requests)** does not affect test behavior.

### New Test Files and Functions

| Test File                                         | Class                          | Function                                              | WU  |
| ------------------------------------------------- | ------------------------------ | ----------------------------------------------------- | --- |
| `tests/test_integration/test_audit_triggers.py`   | `TestAuditTriggerInsert`       | `test_insert_transaction_creates_audit_row`           | 1   |
|                                                   |                                | `test_insert_captures_new_data`                       | 1   |
|                                                   |                                | `test_insert_old_data_is_null`                        | 1   |
|                                                   |                                | `test_insert_captures_row_id`                         | 1   |
|                                                   |                                | `test_insert_on_account_creates_audit_row`            | 1   |
|                                                   |                                | `test_insert_on_salary_profile_creates_audit_row`     | 1   |
|                                                   |                                | `test_insert_on_auth_user_settings_creates_audit_row` | 1   |
|                                                   | `TestAuditTriggerUpdate`       | `test_update_transaction_creates_audit_row`           | 1   |
|                                                   |                                | `test_update_captures_changed_fields`                 | 1   |
|                                                   |                                | `test_update_captures_old_and_new_data`               | 1   |
|                                                   |                                | `test_update_no_change_skips_audit`                   | 1   |
|                                                   |                                | `test_update_on_account_creates_audit_row`            | 1   |
|                                                   | `TestAuditTriggerDelete`       | `test_delete_transaction_creates_audit_row`           | 1   |
|                                                   |                                | `test_delete_captures_old_data`                       | 1   |
|                                                   |                                | `test_delete_new_data_is_null`                        | 1   |
|                                                   | `TestAuditTriggerMetadata`     | `test_table_schema_and_name_captured`                 | 1   |
|                                                   |                                | `test_executed_at_is_populated`                       | 1   |
|                                                   |                                | `test_db_user_is_populated`                           | 1   |
|                                                   |                                | `test_user_id_is_null_without_middleware`             | 1   |
|                                                   | `TestAuditTriggerAllTables`    | `test_trigger_exists_on_all_tables`                   | 1   |
|                                                   | `TestAuditUserIdCapture`       | `test_authenticated_request_captures_user_id`         | 2   |
|                                                   |                                | `test_unauthenticated_request_has_null_user_id`       | 2   |
|                                                   |                                | `test_set_local_is_transaction_scoped`                | 2   |
| `tests/test_utils/test_log_events.py`             | `TestLogEvent`                 | `test_log_event_emits_at_correct_level`               | 3   |
|                                                   |                                | `test_log_event_includes_event_field`                 | 3   |
|                                                   |                                | `test_log_event_includes_category_field`              | 3   |
|                                                   |                                | `test_log_event_includes_extra_kwargs`                | 3   |
|                                                   |                                | `test_log_event_message_is_human_readable`            | 3   |
|                                                   | `TestEventCategories`          | `test_auth_category_is_string`                        | 3   |
|                                                   |                                | `test_business_category_is_string`                    | 3   |
|                                                   |                                | `test_error_category_is_string`                       | 3   |
|                                                   |                                | `test_performance_category_is_string`                 | 3   |
| `tests/test_utils/test_logging_config.py`         | `TestRequestIdHeader`          | `test_response_includes_x_request_id`                 | 4   |
|                                                   |                                | `test_x_request_id_is_uuid4`                          | 4   |
|                                                   |                                | `test_x_request_id_matches_log_request_id`            | 4   |
|                                                   | `TestRequestDurationLogLevel`  | `test_fast_request_logs_at_debug`                     | 4   |
|                                                   |                                | `test_slow_request_logs_at_warning`                   | 4   |
|                                                   |                                | `test_slow_threshold_configurable`                    | 4   |
|                                                   | `TestRequestLogFields`         | `test_log_includes_remote_addr`                       | 4   |
|                                                   |                                | `test_log_includes_user_id_when_authenticated`        | 4   |
|                                                   |                                | `test_log_excludes_user_id_when_anonymous`            | 4   |
| `tests/test_scripts/test_audit_cleanup.py`        | `TestAuditCleanup`             | `test_cleanup_deletes_old_rows`                       | 5   |
|                                                   |                                | `test_cleanup_preserves_recent_rows`                  | 5   |
|                                                   |                                | `test_cleanup_dry_run_does_not_delete`                | 5   |
|                                                   |                                | `test_cleanup_returns_correct_count`                  | 5   |
|                                                   |                                | `test_cleanup_with_zero_days_deletes_all`             | 5   |
|                                                   |                                | `test_cleanup_with_empty_table`                       | 5   |
| `tests/test_performance/test_trigger_overhead.py` | `TestRecurrenceEngineOverhead` | `test_generate_for_template_overhead`                 | 6   |
|                                                   |                                | `test_regenerate_for_template_overhead`               | 6   |
|                                                   |                                | `test_bulk_transaction_insert_overhead`               | 6   |

**Total new tests: 46** (43 in normal test suite + 3 performance tests run separately)

---

## Phase 8B Test Gate Checklist (Expanded)

From the Phase 8 plan's test gate (lines 274-283), mapped to specific tests:

- [ ] **`pytest` passes (all existing tests)**
  - Verified: triggers are additive, `system.audit_log` truncated between tests, no test logic changes.

- [ ] **Audit triggers fire: INSERT/UPDATE/DELETE on `budget.transactions` produce rows in `system.audit_log`**
  - `TestAuditTriggerInsert.test_insert_transaction_creates_audit_row`
  - `TestAuditTriggerUpdate.test_update_transaction_creates_audit_row`
  - `TestAuditTriggerDelete.test_delete_transaction_creates_audit_row`

- [ ] **Audit log captures changed_fields on UPDATE (only the columns that changed)**
  - `TestAuditTriggerUpdate.test_update_captures_changed_fields`
  - `TestAuditTriggerUpdate.test_update_no_change_skips_audit`

- [ ] **Audit log captures user_id from the application context**
  - `TestAuditUserIdCapture.test_authenticated_request_captures_user_id`
  - `TestAuditUserIdCapture.test_unauthenticated_request_has_null_user_id`

- [ ] **`scripts/audit_cleanup.py` deletes rows older than the configured retention period**
  - `TestAuditCleanup.test_cleanup_deletes_old_rows`
  - `TestAuditCleanup.test_cleanup_preserves_recent_rows`
  - `TestAuditCleanup.test_cleanup_returns_correct_count`

- [ ] **Application logs output structured JSON in production config**
  - Pre-existing: `app/utils/logging_config.py` already outputs JSON via `python-json-logger`. Verified in pre-existing infrastructure audit.

- [ ] **Every log entry includes request_id, timestamp, level**
  - Pre-existing: `RequestIdFilter` (line 21), `JsonFormatter` with `timestamp=True` (line 86), `level` field renamed from `levelname` (line 83).
  - `TestRequestIdHeader.test_x_request_id_is_uuid4`

- [ ] **Request duration is logged for every request**
  - Pre-existing: `_log_request_summary()` (line 107) logs `request_duration` in milliseconds.
  - `TestRequestDurationLogLevel.test_fast_request_logs_at_debug`
  - `TestRequestDurationLogLevel.test_slow_request_logs_at_warning`

- [ ] **Auth events (login, logout, password change) appear in logs**
  - `TestLogEvent.test_log_event_emits_at_correct_level` (verifies the helper works)
  - Manual verification: login/logout/password-change routes use `log_event()` with category=AUTH.

- [ ] **Recurrence engine regeneration with triggers: overhead is acceptable (less than 20% slower)**
  - `TestRecurrenceEngineOverhead.test_generate_for_template_overhead`
  - `TestRecurrenceEngineOverhead.test_regenerate_for_template_overhead`

---

## File Summary

### New Files (8)

| File                                                       | Type                | WU  |
| ---------------------------------------------------------- | ------------------- | --- |
| `migrations/versions/<hash>_add_audit_log_and_triggers.py` | Alembic migration   | 1   |
| `app/utils/log_events.py`                                  | Python module       | 3   |
| `scripts/audit_cleanup.py`                                 | CLI script          | 5   |
| `scripts/benchmark_triggers.py`                            | CLI script (manual) | 6   |
| `monitoring/promtail-config.yml`                           | Promtail config     | 7   |
| `monitoring/README.md`                                     | Documentation       | 7   |
| `tests/test_performance/conftest.py`                       | Test fixtures       | 6   |
| `tests/test_performance/__init__.py`                       | Package init        | 6   |

### New Test Files (4)

| File                                              | Tests | WU   |
| ------------------------------------------------- | ----- | ---- |
| `tests/test_integration/test_audit_triggers.py`   | 23    | 1, 2 |
| `tests/test_utils/test_log_events.py`             | 9     | 3    |
| `tests/test_utils/test_logging_config.py`         | 9     | 4    |
| `tests/test_scripts/test_audit_cleanup.py`        | 6     | 5    |
| `tests/test_performance/test_trigger_overhead.py` | 3     | 6    |

### Modified Files (7)

| File                                    | Changes                                                                                                                                                             | WU   |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- |
| `tests/conftest.py`                     | Add `TRUNCATE system.audit_log` to test cleanup                                                                                                                     | 1    |
| `app/utils/logging_config.py`           | Add `SET LOCAL` middleware in `_attach_request_id()`; enhance `_log_request_summary()` with `X-Request-Id` header, `user_id`, `remote_addr`, conditional log levels | 2, 4 |
| `app/routes/auth.py`                    | Refactor 9 log calls to use `log_event()`                                                                                                                           | 3    |
| `app/services/recurrence_engine.py`     | Refactor transaction generation log to use `log_event()`                                                                                                            | 3    |
| `app/services/carry_forward_service.py` | Refactor carry-forward log to use `log_event()`                                                                                                                     | 3    |
| `app/config.py`                         | Add `SLOW_REQUEST_THRESHOLD_MS`, `AUDIT_RETENTION_DAYS` config vars                                                                                                 | 4    |
| `.env.example`                          | Add `LOG_LEVEL`, `SLOW_REQUEST_THRESHOLD_MS`, `AUDIT_RETENTION_DAYS` entries                                                                                        | 4    |
| `entrypoint.sh`                         | Update Gunicorn flags: disable access log, enable error log to stdout                                                                                               | 7    |
| `docker-compose.yml`                    | Add `LOG_LEVEL`, `SLOW_REQUEST_THRESHOLD_MS`, `AUDIT_RETENTION_DAYS`, `TOTP_ENCRYPTION_KEY` env vars                                                                | 7    |
