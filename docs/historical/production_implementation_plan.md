> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Shekel Production Readiness Implementation Plan

**Date:** 2026-03-22
**Validated against:** commit `e636bf6` on branch `dev`
**Audit source:** `docs/production_readiness_audit_v2.md` (2026-03-21)
**Audience:** Solo developer/tester (no separate QA team)

---

## Pre-Plan Checklist

- [x] Read `docs/production_readiness_audit_v2.md` in full
- [x] Read `docs/project_requirements_v2.md` in full
- [x] Read `docs/project_requirements_v3_addendum.md` in full
- [x] Opened and read every file referenced in the audit's findings
- [x] Confirmed each audit finding still exists (or is resolved) in the code
- [x] Checked for issues the audit may have missed

### Resolved Since Audit

The following audit findings have been fixed and are **excluded from this plan**:

| ID  | Finding                                  | Status                                                                                                                                                                                                                                                                                                   |
| --- | ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| B2  | No automated database backup             | RESOLVED -- `scripts/backup.sh` and `scripts/backup_retention.sh` exist with full documentation in `docs/backup_runbook.md`                                                                                                                                                                              |
| M4  | Python 3.14 is pre-release               | INCORRECT -- Python 3.14 reached GA on 2025-10-07. Python 3.14.3 is the latest stable release (2026-02-03). The `python:3.14-slim` Docker image is production-grade. The CI's `allow-prereleases: true` is a leftover that should be removed (see P3-4), but the Python version itself is not a concern. |
| L5  | Personal email in `scripts/reset_mfa.py` | RESOLVED -- now uses `admin@shekel.local`                                                                                                                                                                                                                                                                |
| L6  | `MfaConfig` docstring stale              | RESOLVED -- audit itself confirmed docstring is correct                                                                                                                                                                                                                                                  |

### Not Actionable (Informational Only)

| ID  | Finding                                   | Reason                                                                                                                                                                                                              |
| --- | ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| H2  | Grid queries rely on transitive safety    | Not a vulnerability -- queries are scoped by user-owned scenario (`grid.py:44-47`) and user-owned period IDs (`grid.py:74`). Latent risk only. Covered by existing `test_integration/test_data_isolation.py` tests. |
| L2  | `import-outside-toplevel` pylint disables | Standard Flask application factory practice. Not a code smell.                                                                                                                                                      |
| L3  | `broad-except` in chart routes            | Appropriate for optional UI components. Exceptions are logged.                                                                                                                                                      |

---

## Phase 0: Verification Baseline

Before any changes, establish that the current state is stable.

### P0-1: Run the full test suite

**File(s):** N/A (test infrastructure)
**Effort:** trivial (wait ~9 minutes)

**Action:**

```bash
timeout 660 pytest -v --tb=short 2>&1 | tee docs/baseline_test_results.txt
```

**Verification:**
Record the total number of tests, passes, failures, and errors. All tests should pass. If any fail, investigate and document before proceeding. Do not start Phase 1 with a red test suite.

**Dependencies:** None

### P0-2: Run pylint and record results

**File(s):** N/A
**Effort:** trivial

**Action:**

```bash
pylint app/ 2>&1 | tail -5
```

**Verification:**
Record the score. This is the baseline for Phase 4 items.

**Dependencies:** None

### P0-3: Verify Docker Compose build and startup

**File(s):** `docker-compose.yml`, `Dockerfile`, `entrypoint.sh`
**Effort:** small

**Action:**
Build and start the Docker stack. Verify all three containers (db, app, nginx) reach healthy status.

```bash
docker compose up -d
docker compose ps   # all should show "healthy"
```

**Verification:**

- `curl http://localhost/health` returns `{"status": "healthy", "database": "connected"}`
- All three services show healthy in `docker compose ps`

**Dependencies:** None

### P0-4: Verify core workflow

**File(s):** N/A (manual test)
**Effort:** small

**Action:**

1. Log in with seed credentials
2. Navigate to the budget grid -- verify it loads with data
3. Click an empty cell and create a transaction -- verify it appears
4. Delete the test transaction -- verify it disappears

**Verification:**
All four steps complete without errors. This confirms the app is functional before changes begin.

**Dependencies:** P0-3

### P0-5: Record the baseline commit hash

**File(s):** This document
**Effort:** trivial

**Action:**
The plan was validated against commit `e636bf6`. Record this so any changes can be diffed against it.

**Dependencies:** None

---

## Phase 1: Critical Blockers (Must Fix Before Any Production Use)

### P1-1: Add `is_active` check to `user_loader`

**Source:** B1
**File(s):** `app/__init__.py:65-78`
**Severity:** CRITICAL
**Effort:** trivial (under 5 min)

**Problem:**
The `load_user` callback at line 65-78 returns the user object without checking `user.is_active`. Flask-Login's `UserMixin.is_authenticated` unconditionally returns `True`, so a deactivated user retains full access through existing sessions until the cookie expires (30 days).

```python
user = db.session.get(User, int(user_id))
if user is None:
    return None
# ... session_invalidated_at check ...
return user  # <-- no is_active check
```

**Fix:**
Add `if not user.is_active: return None` immediately after the `if user is None` check (after line 67). This causes Flask-Login to treat deactivated users as unauthenticated, forcing re-login which `authenticate()` in `auth_service.py:303` will correctly reject.

**Verification:**
Write a test in `tests/test_routes/test_auth.py`:

1. Log in as a user (verify 200 on grid)
2. Set `user.is_active = False` and commit
3. Make another request to the grid
4. Assert redirect to `/login`

**Dependencies:** None

### P1-2: Add `scenario_id` filter to carry-forward service

**Source:** B3
**File(s):** `app/services/carry_forward_service.py:62-69`
**Severity:** CRITICAL
**Effort:** small (under 30 min)

**Problem:**
The query that finds projected transactions to carry forward does not filter by `scenario_id`:

```python
projected_txns = (
    db.session.query(Transaction)
    .filter(
        Transaction.pay_period_id == source_period_id,
        Transaction.status_id == projected_status.id,
        Transaction.is_deleted.is_(False),
    )
    .all()
)
```

If scenarios are ever enabled, carry-forward would move transactions from ALL scenarios, corrupting non-baseline data. Currently only baseline scenarios exist, so no data is at risk today -- but this must be fixed before scenarios ship.

**Fix:**

1. Add a `scenario_id` parameter to the `carry_forward()` function signature (after `user_id`).
2. Add `.filter(Transaction.scenario_id == scenario_id)` to the query at line 64.
3. Update the route caller in `app/routes/grid.py` to pass `scenario.id`.

**Verification:**
Update `tests/test_services/test_carry_forward_service.py`:

1. Create two scenarios for the same user with transactions in the same period.
2. Call `carry_forward()` with one scenario's ID.
3. Assert only that scenario's transactions moved; the other scenario's transactions remain untouched.

**Dependencies:** None

### P1-3: Refuse default password in production seed

**Source:** B4
**File(s):** `scripts/seed_user.py:45-46`
**Severity:** HIGH
**Effort:** trivial (under 5 min)

**Problem:**
If `SEED_USER_EMAIL` is set but `SEED_USER_PASSWORD` is not (or is empty), and the script is invoked directly (outside docker-compose), `os.getenv("SEED_USER_PASSWORD", "ChangeMe!2026")` falls back to the publicly documented default password. The docker-compose path passes an empty string which fails the length check, but direct invocation does not.

```python
email = os.getenv("SEED_USER_EMAIL", "admin@shekel.local")
password = os.getenv("SEED_USER_PASSWORD", "ChangeMe!2026")
```

**Fix:**
After line 46, add a check:

```python
flask_env = os.getenv("FLASK_ENV", "development")
if flask_env == "production" and password == "ChangeMe!2026":
    print("Error: SEED_USER_PASSWORD must be changed from the default in production.")
    sys.exit(1)
```

**Verification:**
Write a test in `tests/test_scripts/` or verify manually:

1. Set `FLASK_ENV=production` and unset `SEED_USER_PASSWORD`
2. Run `python scripts/seed_user.py`
3. Assert exit code 1 with the error message

**Dependencies:** None

### P1-4: Add timeout to `pg_isready` loop

**Source:** M2
**File(s):** `entrypoint.sh:31-33`
**Severity:** HIGH
**Effort:** trivial (under 5 min)

**Problem:**
The `pg_isready` loop has no counter or maximum wait time:

```bash
until pg_isready -h "${DB_HOST:-db}" -p "${DB_PORT:-5432}" -U "${DB_USER:-shekel_user}" -q; do
    sleep 1
done
```

If `DB_HOST` is misconfigured, the container hangs indefinitely. Docker's `start_period` on the healthcheck provides some mitigation, but the script itself should fail explicitly.

**Fix:**
Replace lines 31-33 with:

```bash
MAX_RETRIES=60
RETRY_COUNT=0
until pg_isready -h "${DB_HOST:-db}" -p "${DB_PORT:-5432}" -U "${DB_USER:-shekel_user}" -q; do
    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ "$RETRY_COUNT" -ge "$MAX_RETRIES" ]; then
        echo "ERROR: PostgreSQL not ready after ${MAX_RETRIES} seconds."
        exit 1
    fi
    sleep 1
done
```

**Verification:**

1. Set `DB_HOST=nonexistent` in the app container environment
2. Run `docker compose up app`
3. Verify the container exits after ~60 seconds with the error message (not hangs forever)
4. Restore correct `DB_HOST` and verify normal startup still works

**Dependencies:** None

---

## Phase 2: Security and Data Isolation (Must Fix Before Multi-User Use)

### P2-1: Add ownership check to `get_quick_create`

**Source:** H1
**File(s):** `app/routes/transactions.py:220-223`
**Severity:** MEDIUM
**Effort:** trivial (under 5 min)

**Problem:**
`get_quick_create` loads `Category` and `PayPeriod` by ID from query parameters without verifying the requesting user owns them:

```python
category = db.session.get(Category, category_id)
period = db.session.get(PayPeriod, period_id)
if not category or not period:
    return "Not found", 404
```

An attacker could enumerate IDs to discover another user's category names and pay period dates.

**Fix:**
Replace the existence check (lines 222-223) with:

```python
if not category or category.user_id != current_user.id:
    return "Not found", 404
if not period or period.user_id != current_user.id:
    return "Not found", 404
```

**Verification:**
Write a test in `tests/test_routes/test_transaction_auth.py`:

1. Create two users, each with their own category and pay period
2. Log in as User A
3. GET `/transactions/new/quick?category_id=<B's category>&period_id=<B's period>&txn_type_name=expense`
4. Assert 404 response

**Dependencies:** None

### P2-2: Add ownership check to `get_full_create`

**Source:** H1
**File(s):** `app/routes/transactions.py:256-258`
**Severity:** MEDIUM
**Effort:** trivial (under 5 min)

**Problem:**
Same pattern as P2-1. `get_full_create` loads `Category` and `PayPeriod` by ID without ownership check.

**Fix:**
Replace the existence check (lines 258-259) with:

```python
if not category or category.user_id != current_user.id:
    return "Not found", 404
if not period or period.user_id != current_user.id:
    return "Not found", 404
```

**Verification:**
Same test approach as P2-1. Add a test case for `/transactions/new/full` with another user's IDs. Assert 404.

**Dependencies:** None

### P2-3: Add ownership check to `get_empty_cell`

**Source:** H1
**File(s):** `app/routes/transactions.py:294-296`
**Severity:** MEDIUM
**Effort:** trivial (under 5 min)

**Problem:**
Same pattern as P2-1. `get_empty_cell` loads `Category` and `PayPeriod` by ID without ownership check.

**Fix:**
Replace the existence check (lines 296-297) with:

```python
if not category or category.user_id != current_user.id:
    return "Not found", 404
if not period or period.user_id != current_user.id:
    return "Not found", 404
```

**Verification:**
Same test approach as P2-1. Add a test case for `/transactions/empty-cell` with another user's IDs. Assert 404.

**Dependencies:** None

### P2-4: Add ownership check to `preview_recurrence`

**Source:** H3
**File(s):** `app/routes/templates.py:419-421`
**Severity:** MEDIUM
**Effort:** trivial (under 5 min)

**Problem:**
The `preview_recurrence` endpoint loads a `PayPeriod` by ID from query parameters without verifying ownership:

```python
start_period = db.session.get(PayPeriod, start_period_id)
if start_period:
    effective_from = start_period.start_date
```

An attacker could pass another user's period ID to trigger pattern matching on their pay period schedule.

**Fix:**
Replace line 420 with:

```python
if start_period and start_period.user_id == current_user.id:
```

If the period belongs to another user (or doesn't exist), the code falls through to the `if effective_from is None:` block at line 425, which correctly uses the current user's periods.

**Verification:**
Write a test in `tests/test_routes/test_templates.py`:

1. Create two users with different pay period schedules
2. Log in as User A
3. POST `preview_recurrence` with `start_period_id` set to User B's period
4. Assert the response uses User A's period schedule (not User B's)

**Dependencies:** None

### P2-5: Add rate limiting to registration

**Source:** H5
**File(s):** `app/routes/auth.py:133`
**Severity:** MEDIUM
**Effort:** trivial (under 5 min)

**Problem:**
The `/register` POST endpoint at line 133 has no rate limiting, unlike `/login` which has `@limiter.limit("5 per 15 minutes")`. An attacker could create unlimited accounts.

**Fix:**
Add the rate limiter decorator to the `register` function (before line 133):

```python
@auth_bp.route("/register", methods=["POST"])
@limiter.limit("3 per hour", methods=["POST"])
def register():
```

**Verification:**

1. Manually verify that `from app.extensions import limiter` is already imported in `auth.py`
2. Write a test (with rate limiting enabled in test config or using the `@limiter.limit` mock) that verifies the 4th registration attempt within an hour returns 429

Note: The test config has `RATELIMIT_ENABLED = False` (`config.py:70`), so this test may need to temporarily enable rate limiting or test the decorator's presence via inspection.

**Dependencies:** None

### P2-6: Add admin-controlled registration toggle

**Source:** H6
**File(s):** `app/routes/auth.py:125-159`
**Severity:** MEDIUM
**Effort:** medium (1-2 hours)

**Problem:**
Anyone who can reach the app can create an account at `/register`. For LAN-only deployment this is acceptable, but for external access (Cloudflare Tunnel) this means anyone with the URL can register.

**Fix:**
Add an environment variable `REGISTRATION_ENABLED` (default: `true`) checked in the register routes:

1. In `app/config.py` BaseConfig, add: `REGISTRATION_ENABLED = os.getenv("REGISTRATION_ENABLED", "true").lower() == "true"`
2. In `app/routes/auth.py`, both `register_form()` (line 126) and `register()` (line 134), add at the top:
   ```python
   if not current_app.config.get("REGISTRATION_ENABLED", True):
       abort(404)
   ```
3. Conditionally hide the "Register" link in the login template when registration is disabled.
4. Add `REGISTRATION_ENABLED` to `.env.example` with documentation.

**Verification:**
Write tests:

1. With `REGISTRATION_ENABLED=true`: GET `/register` returns 200, POST creates user
2. With `REGISTRATION_ENABLED=false`: GET `/register` returns 404, POST returns 404

**Dependencies:** None

---

## Phase 3: Operational Hardening (Fix Soon After Launch)

### P3-1: Sanitize health check error response

**Source:** M5
**File(s):** `app/routes/health.py:39-42`
**Severity:** MEDIUM
**Effort:** trivial (under 5 min)

**Problem:**
The health endpoint returns `str(exc)` to unauthenticated callers when the database check fails:

```python
return jsonify({
    "status": "unhealthy",
    "database": "error",
    "detail": str(exc),  # Could leak connection info
}), 500
```

**Fix:**
Remove the `"detail"` field from the JSON response. The exception is already logged at line 38 (`logger.error("Health check failed: %s", exc)`), so the detail is available in logs.

```python
return jsonify({
    "status": "unhealthy",
    "database": "error",
}), 500
```

**Verification:**
Update `tests/test_routes/test_health.py`:

1. Mock the database query to raise an exception
2. Assert the response JSON does NOT contain a `"detail"` key
3. Assert the response status is 500

**Dependencies:** None

### P3-2: Scope category delete in-use check by user

**Source:** M6
**File(s):** `app/routes/categories.py:92-100`
**Severity:** MEDIUM
**Effort:** trivial (under 5 min)

**Problem:**
The in-use check queries `TransactionTemplate` and `Transaction` by `category_id` without filtering by `user_id`:

```python
in_use = (
    db.session.query(TransactionTemplate)
    .filter_by(category_id=category_id)
    .first()
) or (
    db.session.query(Transaction)
    .filter_by(category_id=category_id)
    .first()
)
```

If another user's template happens to reference a category with the same ID (possible via direct DB manipulation or shared reference data), the current user would be incorrectly blocked from deleting their own category.

**Fix:**
Add `user_id` filter to the `TransactionTemplate` query:

```python
in_use = (
    db.session.query(TransactionTemplate)
    .filter_by(category_id=category_id, user_id=current_user.id)
    .first()
) or (
    db.session.query(Transaction)
    .filter_by(category_id=category_id)
    .first()
)
```

Note: `Transaction` does not have a direct `user_id` column. Filtering transactions by user requires a join through `pay_period`. For the current single-user setup, the unscoped Transaction query is not a data leak (it only blocks deletion, never reveals data). A more thorough fix would join through PayPeriod, but that is lower priority:

```python
db.session.query(Transaction)
    .join(PayPeriod, Transaction.pay_period_id == PayPeriod.id)
    .filter(PayPeriod.user_id == current_user.id, Transaction.category_id == category_id)
    .first()
```

**Verification:**
Write a test in `tests/test_routes/test_categories.py`:

1. Create two users, each with their own category
2. Create a template for User B using User B's category
3. Log in as User A and delete User A's category (which has the same `category_id` concept but different actual ID)
4. Assert deletion succeeds (not blocked by User B's template)

**Dependencies:** None

### P3-3: Restrict `forwarded_allow_ips` to Docker subnet

**Source:** M3
**File(s):** `gunicorn.conf.py:79`
**Severity:** LOW
**Effort:** trivial (under 5 min)

**Problem:**
`forwarded_allow_ips = "*"` trusts `X-Forwarded-*` headers from any source. Safe in the current Docker architecture (Gunicorn only on internal network), but dangerous if the architecture changes.

**Fix:**
Replace line 79 with:

```python
forwarded_allow_ips = os.getenv("FORWARDED_ALLOW_IPS", "172.16.0.0/12,192.168.0.0/16,10.0.0.0/8")
```

Add `import os` at the top if not already present. This restricts trust to RFC 1918 private subnets (which cover all Docker bridge networks) while allowing override via environment variable.

**Verification:**

1. Verify `docker compose up` still works -- Nginx's `X-Forwarded-For` is still trusted
2. Verify `gunicorn.conf.py` contains the restricted IPs

**Dependencies:** None

### P3-4: Pin Python 3.14 patch version in Dockerfile

**Source:** M4 (audit finding invalidated -- Python 3.14 reached GA on 2025-10-07)
**File(s):** `Dockerfile:6, 20`
**Severity:** LOW
**Effort:** trivial (under 5 min)

**Problem:**
The audit claimed Python 3.14 was pre-release. This is incorrect -- Python 3.14 reached GA on October 7, 2025, and 3.14.3 is the latest stable release (February 3, 2026). The current `python:3.14-slim` tag is a stable production image and no downgrade is needed.

However, the Dockerfile uses `python:3.14-slim` without a patch version, which means a `docker compose build` at different times could pull different patch releases (3.14.1 vs 3.14.3), potentially introducing inconsistencies.

**Fix:**
Pin to a specific patch version for reproducible builds:

- Line 6: `FROM python:3.14.3-slim AS builder`
- Line 20: `FROM python:3.14.3-slim`

Also remove `allow-prereleases: true` from `.github/workflows/ci.yml` since it is no longer needed.

**Verification:**

1. `docker compose build` succeeds
2. `docker compose up` -- app starts and `/health` returns healthy
3. `docker exec shekel-app python --version` shows `3.14.3`

**Dependencies:** None

### P3-5: Add stale anchor warning to balance calculator

**Source:** M1
**File(s):** `app/services/balance_calculator.py:297-323`
**Severity:** MEDIUM
**Effort:** medium (1-2 hours)

**Problem:**
The `_sum_all()` function for post-anchor periods excludes ALL done/received transactions:

```python
if status_name in ("credit", "cancelled", "done", "received"):
    continue
```

This is documented as intentional -- done/received items are assumed to be reflected in the anchor balance. But if a user marks transactions as "done" in future periods without updating the anchor, projected balances will be too high (the done expense becomes invisible).

**Fix:**
Rather than changing the balance calculation semantics (which would break the anchor-based model), add a UI warning. In the balance calculator's return data, include a flag when done/received transactions exist in periods after the anchor period:

1. In `calculate_balances()`, after computing period balances, check if any post-anchor period has done/received transactions.
2. If so, add a `stale_anchor_warning` flag to the return value.
3. In `app/templates/grid/grid.html`, display a dismissible warning when this flag is set: "Some transactions are marked done in future periods. Update your anchor balance for accurate projections."

**Verification:**
Write a test in `tests/test_services/test_balance_calculator.py`:

1. Set anchor at period 5
2. Mark a transaction as "done" in period 7
3. Call `calculate_balances()`
4. Assert the result contains `stale_anchor_warning=True`

Also test the inverse: no done transactions in post-anchor periods returns `stale_anchor_warning=False`.

**Dependencies:** None

---

## Phase 4: Code Quality and Architecture (Improve Over Time)

### P4-1: Extract `_load_tax_configs` to a shared service

**Source:** L1
**File(s):** `app/routes/salary.py:64`, `app/routes/retirement.py:110`, `app/services/chart_data_service.py:626`
**Severity:** LOW
**Effort:** small (under 30 min)

**Problem:**
The `_load_tax_configs` function is defined in `app/routes/salary.py:64` and imported by `app/routes/retirement.py:110` -- a route-to-route import that violates the layered architecture. Additionally, an identical copy exists at `app/services/chart_data_service.py:626`.

**Fix:**

1. Create `app/services/tax_config_service.py` with the `load_tax_configs()` function (moved from `salary.py:64`).
2. Update all three call sites to import from the new service:
   - `app/routes/salary.py` (lines 119, 536, 579, 715)
   - `app/routes/retirement.py:110-111`
   - `app/services/chart_data_service.py:761` (delete the duplicate at line 626)
3. Remove the original `_load_tax_configs` from `salary.py` and `chart_data_service.py`.

**Verification:**

1. Run `grep -rn "_load_tax_configs" app/` -- should only appear in the new service file
2. Run `pytest tests/test_routes/test_salary.py tests/test_routes/test_retirement.py tests/test_services/test_chart_data_service.py -v` -- all pass

**Dependencies:** None

### P4-2: Add ownership guard to `resolve_conflicts` in recurrence_engine

**Source:** H4
**File(s):** `app/services/recurrence_engine.py:248-272`
**Severity:** LOW
**Effort:** small (under 30 min)

**Problem:**
`resolve_conflicts()` loads transactions by ID without verifying ownership:

```python
for txn_id in transaction_ids:
    txn = db.session.get(Transaction, txn_id)
    if txn is None:
        continue
    txn.is_override = False
```

Not currently reachable from any route endpoint, but if ever connected, it would be an IDOR vulnerability.

**Fix:**
Add a `user_id` parameter and ownership check:

```python
def resolve_conflicts(transaction_ids, action, user_id, new_amount=None):
    # ...
    for txn_id in transaction_ids:
        txn = db.session.get(Transaction, txn_id)
        if txn is None:
            continue
        if txn.pay_period.user_id != user_id:
            continue
        # ... rest of logic
```

**Verification:**
Update `tests/test_services/test_recurrence_engine.py::TestResolveConflicts`:

1. Create a transaction owned by User A
2. Call `resolve_conflicts([txn.id], "update", user_id=user_b.id, new_amount=...)`
3. Assert the transaction was NOT modified (ownership check blocked it)

**Dependencies:** None

### P4-3: Add ownership guard to `resolve_conflicts` in transfer_recurrence

**Source:** New finding (same pattern as H4)
**File(s):** `app/services/transfer_recurrence.py:190-210`
**Severity:** LOW
**Effort:** small (under 30 min)

**Problem:**
Same pattern as P4-2. `transfer_recurrence.resolve_conflicts()` loads transfers by ID without ownership verification.

**Fix:**
Add a `user_id` parameter and ownership check:

```python
def resolve_conflicts(transfer_ids, action, user_id, new_amount=None):
    # ...
    for xfer_id in transfer_ids:
        xfer = db.session.get(Transfer, xfer_id)
        if xfer is None:
            continue
        if xfer.user_id != user_id:
            continue
```

**Verification:**
Update `tests/test_services/test_transfer_recurrence.py::TestResolveConflicts`:

1. Create a transfer owned by User A
2. Call `resolve_conflicts([xfer.id], "update", user_id=user_b.id, new_amount=...)`
3. Assert the transfer was NOT modified

**Dependencies:** None

### P4-4: Add `SQLALCHEMY_ENGINE_OPTIONS` to ProdConfig

**Source:** L4
**File(s):** `app/config.py:87-106`
**Severity:** LOW
**Effort:** trivial (under 5 min)

**Problem:**
`ProdConfig` does not set `SQLALCHEMY_ENGINE_OPTIONS`. SQLAlchemy defaults (`pool_size=5`, `max_overflow=10`) are fine for 2 Gunicorn workers but should be explicitly configured for clarity and future scaling.

**Fix:**
Add to `ProdConfig` class body:

```python
SQLALCHEMY_ENGINE_OPTIONS = {
    "pool_size": 5,
    "max_overflow": 2,
    "pool_timeout": 30,
    "pool_recycle": 1800,
    "connect_args": {"connect_timeout": 5},
}
```

**Verification:**

1. `docker compose up` -- app starts normally
2. Verify the pool settings are active: add a temporary log line in `create_app()` that prints `app.config["SQLALCHEMY_ENGINE_OPTIONS"]` and confirm values appear in startup logs, then remove the log line

**Dependencies:** None

---

## Phase 5: Testing Gaps

### P5-1: Add IDOR tests for transaction form-rendering endpoints

**Source:** H1 verification gap
**File(s):** `tests/test_routes/test_transaction_auth.py` (new tests)
**Severity:** HIGH
**Effort:** small (under 30 min)

**Problem:**
Existing tests (`test_auth_required.py:52-56`) verify these endpoints require authentication, but no tests verify they reject another user's resource IDs. Without these tests, a regression in P2-1 through P2-3 would go undetected.

**Fix:**
Add a new test class `TestTransactionIDOR` to `tests/test_routes/test_transaction_auth.py`:

```python
class TestTransactionIDOR:
    """Verify transaction form endpoints reject other users' resources."""

    def test_quick_create_rejects_other_users_category(self, ...):
        """GET /transactions/new/quick with another user's category_id returns 404."""

    def test_quick_create_rejects_other_users_period(self, ...):
        """GET /transactions/new/quick with another user's period_id returns 404."""

    def test_full_create_rejects_other_users_category(self, ...):
        """GET /transactions/new/full with another user's category_id returns 404."""

    def test_empty_cell_rejects_other_users_period(self, ...):
        """GET /transactions/empty-cell with another user's period_id returns 404."""
```

Each test should:

1. Create two users with their own categories and periods
2. Log in as User A
3. Request the endpoint with User B's resource IDs
4. Assert 404 response

**Verification:**
Run `pytest tests/test_routes/test_transaction_auth.py -v` -- all new tests pass.

**Dependencies:** P2-1, P2-2, P2-3 (these tests verify those fixes)

### P5-2: Add IDOR test for template preview recurrence

**Source:** H3 verification gap
**File(s):** `tests/test_routes/test_templates.py` (new test)
**Severity:** HIGH
**Effort:** small (under 30 min)

**Problem:**
No test verifies that `preview_recurrence` rejects another user's `start_period_id`.

**Fix:**
Add a test to `tests/test_routes/test_templates.py`:

```python
def test_preview_recurrence_ignores_other_users_period(self, ...):
    """preview_recurrence with another user's period_id falls back to own periods."""
```

The test should:

1. Create User A and User B, each with their own pay periods
2. Log in as User A
3. POST preview_recurrence with `start_period_id` set to User B's period
4. Assert the response uses dates from User A's period schedule

**Verification:**
Run `pytest tests/test_routes/test_templates.py -v -k preview` -- test passes.

**Dependencies:** P2-4 (this test verifies that fix)

### P5-3: Add carry-forward scenario isolation test

**Source:** B3 verification gap
**File(s):** `tests/test_services/test_carry_forward_service.py` (new test)
**Severity:** HIGH
**Effort:** small (under 30 min)

**Problem:**
No test verifies that carry-forward respects `scenario_id` boundaries.

**Fix:**
Add a test:

```python
def test_carry_forward_only_moves_transactions_for_given_scenario(self, ...):
    """Carry forward with scenario A should not touch scenario B's transactions."""
```

The test should:

1. Create one user with two scenarios (baseline + alternative)
2. Create projected transactions in both scenarios for the same source period
3. Call `carry_forward(source, target, user_id, scenario_id=baseline.id)`
4. Assert only baseline transactions moved; alternative scenario transactions remain in source period

**Verification:**
Run `pytest tests/test_services/test_carry_forward_service.py -v` -- all tests pass including the new one.

**Dependencies:** P1-2 (this test verifies that fix)

### P5-4: Add `is_active` deactivation test

**Source:** B1 verification gap
**File(s):** `tests/test_routes/test_auth.py` (new test)
**Severity:** HIGH
**Effort:** small (under 30 min)

**Problem:**
No test verifies that setting `is_active=False` immediately blocks an existing session.

**Fix:**
Add a test:

```python
def test_deactivated_user_is_logged_out(self, ...):
    """A user with is_active=False is forced to re-login on next request."""
```

The test should:

1. Log in as a user (verify grid loads)
2. Set `user.is_active = False` and commit
3. Request the grid again
4. Assert redirect to `/login`
5. Attempt to log in again
6. Assert login fails with "Invalid email or password"

**Verification:**
Run `pytest tests/test_routes/test_auth.py -v -k deactivated` -- test passes.

**Dependencies:** P1-1 (this test verifies that fix)

### P5-5: Add data isolation test for category delete

**Source:** M6 verification gap
**File(s):** `tests/test_routes/test_categories.py` (new test)
**Severity:** MEDIUM
**Effort:** small (under 30 min)

**Problem:**
No test verifies that the category in-use check is scoped by user.

**Fix:**
Add a test:

```python
def test_delete_category_not_blocked_by_other_users_templates(self, ...):
    """User A can delete their category even if User B has templates in a different category."""
```

The test should:

1. Create User A with Category "Test" (no templates referencing it)
2. Create User B with a template referencing User B's own category
3. Log in as User A and delete Category "Test"
4. Assert deletion succeeds (200/redirect, not blocked)

**Verification:**
Run `pytest tests/test_routes/test_categories.py -v` -- test passes.

**Dependencies:** P3-2 (this test verifies that fix)

### P5-6: Add health endpoint error sanitization test

**Source:** M5 verification gap
**File(s):** `tests/test_routes/test_health.py` (new test)
**Severity:** MEDIUM
**Effort:** trivial (under 5 min)

**Problem:**
No test verifies that the health endpoint's error response does not leak exception details.

**Fix:**
Add a test:

```python
def test_unhealthy_response_does_not_expose_details(self, ...):
    """Health check error response should not include exception details."""
```

The test should:

1. Mock `db.session.execute` to raise an exception with a message containing "password=secret"
2. GET `/health`
3. Assert response status is 500
4. Assert "password" is NOT in the response JSON
5. Assert response JSON has only "status" and "database" keys (no "detail")

**Verification:**
Run `pytest tests/test_routes/test_health.py -v` -- test passes.

**Dependencies:** P3-1 (this test verifies that fix)

---

## Phase 6: Documentation and Onboarding

### P6-1: Document `REGISTRATION_ENABLED` in README and `.env.example`

**Source:** H6 fix documentation
**File(s):** `README.md`, `.env.example`
**Severity:** LOW
**Effort:** trivial (under 5 min)

**Problem:**
After P2-6 adds the registration toggle, users need to know it exists and how to use it.

**Fix:**

1. Add `REGISTRATION_ENABLED` to the `.env.example` file with a comment explaining when to set it to `false`.
2. Add a row to the README's Quick Start environment table:
   ```
   | `REGISTRATION_ENABLED` | No | Set to `false` to disable public registration. Default: `true` |
   ```
3. Add a note in the Troubleshooting section: "If `/register` returns 404, check `REGISTRATION_ENABLED` in your `.env`."

**Verification:**
Read the updated README and `.env.example`. Confirm the new variable is documented with clear instructions.

**Dependencies:** P2-6

### P6-2: Add security hardening notes to README

**Source:** New finding
**File(s):** `README.md`
**Severity:** LOW
**Effort:** small (under 30 min)

**Problem:**
The README does not mention security considerations for external access (Cloudflare Tunnel, Tailscale). A user exposing the app externally should know to:

- Disable registration or use an invite code
- Enable MFA
- Use HTTPS (handled by Cloudflare/Tailscale, but worth mentioning)
- Change the default seed password

**Fix:**
Add a "Security" section to the README after "Troubleshooting" with:

- "For LAN-only use: the defaults are sufficient"
- "For external access: set `REGISTRATION_ENABLED=false`, enable MFA for all users, ensure HTTPS termination"
- "Change the default password immediately after first login if you used the seed user"

**Verification:**
Read the updated section. Confirm it covers the three scenarios: LAN, Cloudflare Tunnel, Tailscale.

**Dependencies:** P2-6

### P6-3: Document backup setup in README

**Source:** New finding
**File(s):** `README.md`
**Severity:** MEDIUM
**Effort:** trivial (under 5 min)

**Problem:**
The README does not mention backups at all. The backup infrastructure exists (`scripts/backup.sh`, `docs/backup_runbook.md`) but a new user would not know to set it up. Loss of the Docker volume means loss of all financial data.

**Fix:**
Add a "Backups" subsection to the README after the Quick Start section:

```markdown
### Backups

Shekel stores all data in a PostgreSQL Docker volume. **Set up automated backups before entering real financial data.** See [docs/backup_runbook.md](docs/backup_runbook.md) for:

- Automated daily `pg_dump` backups with retention
- Off-site backup to NAS
- Backup encryption
- Restore procedure
```

**Verification:**
Read the updated README. Confirm the backup section exists and links to the runbook.

**Dependencies:** None

### P6-4: Add `TOTP_ENCRYPTION_KEY` generation instructions to README

**Source:** New finding
**File(s):** `README.md`
**Severity:** LOW
**Effort:** trivial (under 5 min)

**Problem:**
The README Quick Start table says TOTP_ENCRYPTION_KEY is "Only needed before enabling MFA" and points to `.env.example`. A new user who wants MFA must navigate to `.env.example` to find the generation command. The README should include the command directly.

**Fix:**
Expand the TOTP_ENCRYPTION_KEY row in the Quick Start table:

```
| `TOTP_ENCRYPTION_KEY` | No | Required before enabling MFA. Generate with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
```

**Verification:**
Read the updated README. Confirm the generation command is visible without opening `.env.example`.

**Dependencies:** None

---

## Post-Plan Checklist

- [x] Every item cites a real file and line number verified against commit `e636bf6`
- [x] Every item has a concrete verification step
- [x] Every item is atomic (one task, one concern)
- [x] No item is speculative or based on assumptions about the code
- [x] Phases are ordered: blockers (P1) -> security (P2) -> operational (P3) -> quality (P4) -> tests (P5) -> docs (P6)
- [x] Dependencies between items are documented
- [x] The plan accounts for a solo developer with no separate QA team

---

## Summary

| Phase                              | Items  | Estimated Effort                   |
| ---------------------------------- | ------ | ---------------------------------- |
| Phase 0: Verification Baseline     | 5      | ~30 min (mostly waiting for tests) |
| Phase 1: Critical Blockers         | 4      | ~1 hour                            |
| Phase 2: Security & Data Isolation | 6      | ~3 hours                           |
| Phase 3: Operational Hardening     | 5      | ~3 hours                           |
| Phase 4: Code Quality              | 4      | ~1.5 hours                         |
| Phase 5: Testing Gaps              | 6      | ~3 hours                           |
| Phase 6: Documentation             | 4      | ~1 hour                            |
| **Total**                          | **34** | **~12.5 hours**                    |

Phase 1 should be completed before any production use. Phases 1-2 should be completed before sharing with other users. Phases 3-6 can be worked through incrementally after launch.
