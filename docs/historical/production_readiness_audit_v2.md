> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Shekel Production Readiness Audit (v2)

**Date:** 2026-03-21
**Auditor:** Claude Opus 4.6 (comprehensive code review)
**Branch:** `dev` (commit `ed7c63d`)
**Scope:** Financial accuracy, security, data isolation, deployment, testing, code quality

---

## 1. Executive Summary

Shekel is a well-architected personal budget application with strong fundamentals. The layered architecture (routes/services/models) is consistently maintained, financial calculations use `Decimal` arithmetic throughout, authentication uses bcrypt with proper MFA implementation, and CSRF protection covers all forms and HTMX requests. The codebase shows careful attention to defense-in-depth, with ownership checks at both route and service layers.

**For personal use today: Yes, with one caveat.** The core financial logic is correct. The one behavioral issue to understand is the balance calculator's treatment of "done" transactions in post-anchor periods (Section 6, item 6A), which can show incorrect projected balances if the anchor is not updated after marking transactions as done. This is a documented design choice, not a bug, but the user must understand the expected workflow: mark items done, then update the anchor.

**For sharing with coworkers today: Not yet.** Three categories of issues must be addressed first:

1. **Security:** The Flask-Login `user_loader` does not check `is_active`, meaning a deactivated user retains access through existing sessions. Registration has no rate limiting.
2. **Data isolation:** Several form-rendering GET endpoints load resources by ID without verifying ownership (information disclosure). The carry-forward service lacks `scenario_id` filtering.
3. **Operational:** No automated database backup exists. Loss of the Docker volume means loss of all financial data.

The previous audit report (`docs/production_readiness_audit.md`) is stale -- many of its blockers (open redirect, external Docker network, dev deps in prod image, missing error handlers, psycopg2-binary) have been fixed. This report reflects the current state of the codebase.

---

## 2. Blockers (Must Fix Before Production)

### B1. `user_loader` Does Not Check `is_active`

**File:** `app/__init__.py:52-72`
**Severity:** HIGH

The `load_user` callback returns the user object without checking `user.is_active`:

```python
def load_user(user_id):
    user = db.session.get(User, int(user_id))
    if user is None:
        return None
    # ... session_invalidated_at check ...
    return user  # <-- no is_active check
```

In Flask-Login 0.6.3 (the installed version), `UserMixin.is_authenticated` unconditionally returns `True`. The User model overrides `is_active` with a database column, but Flask-Login does NOT check `is_active` in the authentication flow. Result:

- Deactivating a user (`is_active = False`) has no effect on existing sessions
- `@login_required` checks `current_user.is_authenticated` which returns `True` regardless
- The user can continue using the app until their session cookie expires (30 days by default)

The `authenticate()` function in `auth_service.py:303` correctly rejects inactive users on NEW logins, but this does not invalidate existing sessions.

**Fix:** Add `if not user.is_active: return None` after line 60.

### B2. No Automated Database Backup

**Severity:** HIGH

There is no cron job, Docker sidecar, or host-level script that runs `pg_dump` on a schedule. The database lives in a Docker volume (`pgdata`). If the volume is deleted, corrupted, or the host fails, all financial data is permanently lost.

Note: The previous audit report (Section 6) mentions backup scripts at `scripts/backup.sh`. **These scripts do not exist on the `dev` branch.** They may exist on another branch or may have been planned but not implemented.

**Fix:** Create a `pg_dump`-based backup script and schedule it via host cron or a sidecar container. Store backups off-host.

### B3. Carry-Forward Does Not Filter by `scenario_id`

**File:** `app/services/carry_forward_service.py:62-69`
**Severity:** MEDIUM-HIGH

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

No `scenario_id` filter. Currently only baseline scenarios are used, so this has no effect. However, if scenarios are ever enabled, carry-forward would move transactions from ALL scenarios, corrupting non-baseline data.

**Fix:** Accept `scenario_id` as a parameter and add `.filter(Transaction.scenario_id == scenario_id)`.

### B4. Seed Script Default Password in Production

**File:** `scripts/seed_user.py:45-46`
**Severity:** MEDIUM

```python
email = os.getenv("SEED_USER_EMAIL", "admin@shekel.local")
password = os.getenv("SEED_USER_PASSWORD", "ChangeMe!2026")
```

If `SEED_USER_EMAIL` is set but `SEED_USER_PASSWORD` is forgotten, the account is created with the documented default password `ChangeMe!2026`. The `docker-compose.yml:58` uses `${SEED_USER_PASSWORD:-}` (empty string fallback), which means the seed script falls back to its hardcoded default.

**Fix:** Have the seed script refuse to run with the default password when `FLASK_ENV=production`.

---

## 3. High Priority (Fix Before Sharing with Coworkers)

### H1. IDOR in Transaction Form-Rendering Routes

**Files:** `app/routes/transactions.py:220-222`, `:256-257`, `:294-295`
**Severity:** MEDIUM (information disclosure)

Three GET endpoints load `Category` and `PayPeriod` by ID without verifying ownership:

- `get_quick_create` (line 220-222)
- `get_full_create` (line 256-257)
- `get_empty_cell` (line 294-295)

A user could enumerate IDs to discover another user's category names (e.g., "Auto: Car Payment $450") and pay period dates. The data modification endpoints (`create_inline` at line 323-329, `create_transaction` at line 366-367) DO verify ownership, so no data modification is possible.

**Fix:** Add `category.user_id != current_user.id` and `period.user_id != current_user.id` checks.

### H2. Grid Queries Rely on Transitive Safety

**File:** `app/routes/grid.py:83-101`
**Severity:** MEDIUM (systemic risk)

Transaction and transfer queries in the grid use `pay_period_id.in_(period_ids)` where `period_ids` are derived from user-scoped queries. This works correctly but relies on a chain of trust. The Transaction model has no direct `user_id` column, making ownership verification indirect.

The risk is that any future code path that creates a transaction with a wrong `pay_period_id` would silently make it visible to the wrong user. This is a latent risk, not an active vulnerability.

**Fix (Phase 8E):** Consider adding a `user_id` column to the Transaction model, or validate this chain in a dedicated integration test.

### H3. IDOR in Template Preview Recurrence

**File:** `app/routes/templates.py:419-420`
**Severity:** MEDIUM (information disclosure)

The `preview_recurrence` HTMX endpoint accepts a `start_period_id` from query parameters without verifying ownership:

```python
start_period = db.session.get(PayPeriod, start_period_id)
if start_period:  # Only checks existence, not ownership
    effective_from = start_period.start_date
```

An attacker could pass another user's period ID to trigger pattern matching on that user's pay period schedule, leaking information about their period structure.

**Fix:** Add `if start_period and start_period.user_id == current_user.id:`.

### H4. `resolve_conflicts` Has No Ownership Check

**File:** `app/services/recurrence_engine.py:264-265`
**Severity:** LOW (currently not reachable from any route)

The `resolve_conflicts` function loads transactions by ID without verifying ownership. It is not currently wired to any route endpoint. If it's ever connected to a route, it would be an IDOR vulnerability.

**Fix:** Add ownership verification when this function is connected to a route.

### H5. No Rate Limiting on Registration

**File:** `app/routes/auth.py:131-157`
**Severity:** MEDIUM

The `/register` POST endpoint has no rate limiting, unlike `/login` which has `@limiter.limit("5 per 15 minutes")`. An attacker could create unlimited accounts.

**Fix:** Add `@limiter.limit("3 per hour", methods=["POST"])` to the register route.

### H6. Registration Is Open (No Invite Flow)

**File:** `app/routes/auth.py:123-157`
**Severity:** MEDIUM (for coworker sharing)

Anyone who can reach the app can create an account. For LAN-only deployment this is low risk. For external access (Cloudflare Tunnel), this means anyone with the URL can register.

**Fix:** Add an invite code or admin approval mechanism before enabling external access.

---

## 4. Moderate Priority (Fix Soon After Launch)

### M1. Balance Calculator: Done/Received Excluded in Post-Anchor Periods

**File:** `app/services/balance_calculator.py:297-323`
**Severity:** MEDIUM (financial accuracy)

The `_sum_all()` function for post-anchor periods excludes ALL done/received transactions:

```python
if status_name in ("credit", "cancelled", "done", "received"):
    continue
```

If a user marks a transaction as "done" in period N but their anchor is at period M (where M < N), the done transaction becomes invisible to the balance calculation.

**Example:** Anchor at period 5 = $5,000. Period 6 has rent $500 (done) + groceries $200 (projected). Expected balance: $4,300. Calculator shows: $4,800 (rent excluded because "done").

This is documented as intentional and works correctly when the user updates their anchor regularly. But if they don't, balances will be wrong.

**Fix options:** (a) Include done/received in post-anchor calculations using `actual_amount`, or (b) add a UI warning when the anchor is stale.

### M2. `pg_isready` Loop Has No Timeout

**File:** `entrypoint.sh:8-10`

The database readiness check loops forever if the host is misconfigured. Docker Compose's `condition: service_healthy` mitigates this for the normal case, but a wrong `DB_HOST` override would cause an infinite hang.

**Fix:** Add a counter and `exit 1` after ~60 retries.

### M3. `forwarded_allow_ips = "*"` in Gunicorn

**File:** `gunicorn.conf.py:79`

Trusts `X-Forwarded-*` headers from any source. Safe in the current Docker architecture (Gunicorn only on internal network) but dangerous if the architecture changes.

**Fix:** Set to Docker bridge subnet instead of `*`.

### M4. Python 3.14 Is Pre-Release

**File:** `Dockerfile:6`

Python 3.14 is in beta (expected GA October 2026). Running a pre-release in production risks hitting interpreter bugs. The CI uses `allow-prereleases: true` to accommodate this.

**Fix:** Use `python:3.13-slim` until 3.14 GA.

### M5. Health Endpoint Exposes Error Details

**File:** `app/routes/health.py:39-42`

```python
"detail": str(exc),
```

Could leak database connection info (host, port, username) to unauthenticated callers.

**Fix:** Log the full exception server-side; return only `{"status": "unhealthy"}` to clients.

### M6. Category Delete In-Use Check Not User-Scoped

**File:** `app/routes/categories.py:92-100`

The in-use check queries `TransactionTemplate` and `Transaction` by `category_id` without filtering by `user_id`. Not a data leak, but could incorrectly block deletion.

**Fix:** Add `.filter_by(user_id=current_user.id)` to the template query.

---

## 5. Low Priority (Improve Over Time)

### L1. Cross-Route Import (Architecture Violation)

**File:** `app/routes/retirement.py:110`

The retirement route imports `_load_tax_configs` from the salary route:

```python
from app.routes.salary import _load_tax_configs
```

Routes should not import from other routes. This function should be moved to a shared service module (e.g., `app/services/tax_config_service.py`).

### L2. `import-outside-toplevel` Pylint Disables

Multiple files use deferred imports to avoid circular dependencies. This is standard Flask application factory practice. Not a code smell.

### L3. `broad-except` in Chart Routes

**File:** `app/routes/charts.py` (lines 63, 88, 113, 143, 169, 199)

All chart fragments catch `Exception` to render error fragments. Appropriate for optional UI components. Exceptions are logged.

### L4. No Database Connection Pool Tuning

**File:** `app/config.py:87-106`

`ProdConfig` doesn't set `SQLALCHEMY_ENGINE_OPTIONS`. Defaults are fine for 2 workers but should be configured for scaling.

### L5. Personal Email in Documentation

**File:** `scripts/reset_mfa.py:11`

Contains `josh@saltyreformed.com` as a usage example. No security impact.

### L6. `MfaConfig` Docstring May Be Stale

**File:** `app/models/user.py:92-103`

The docstring describes the model accurately. But compare with the previous audit which noted it said "Stub" -- it has been updated since. Currently correct.

---

## 6. Financial Accuracy Findings

### 6A. Balance Calculator: CORRECT (with design caveat)

**File:** `app/services/balance_calculator.py`

Verified correct:

- Anchor balance used as starting point, walks forward chronologically (lines 68-98)
- Income added, expenses subtracted (lines 78-79, 88-89)
- Projected transactions use `estimated_amount` (line 288)
- Credit transactions excluded from checking balance (line 280)
- Cancelled transactions excluded (line 280)
- Transfers: `to_account` = incoming, `from_account` = outgoing (lines 344-347)
- Zero transactions in a period: previous balance carries forward (empty list from `.get()`)
- All arithmetic uses `Decimal` via `Decimal(str(...))` -- no float contamination
- `float()` usage confined to `chart_data_service.py:_to_chart_float()` at the Chart.js presentation boundary only

**Design caveat (M1):** Done/received excluded from post-anchor periods. See Section 4, M1.

### 6B. Paycheck Calculator: CORRECT

**File:** `app/services/paycheck_calculator.py`

Verified correct:

- Order: gross (line 86-88) -> pre-tax deductions (94-97) -> federal tax (115-125) -> state tax (129-134) -> FICA (137-142) -> post-tax deductions (145-148) -> net pay (151-159)
- Pre-tax deductions reduce taxable income before tax calculation (line 100-106)
- Federal withholding via annualized Pub 15-T method with de-annualization (line 105-106, 158-163)
- Third paycheck detection: counts periods starting in the same calendar month (lines 283-300)
- 24-per-year deductions skipped on 3rd paychecks (line 338-339)
- 12-per-year deductions only on first paycheck of month (lines 341-343)
- Raises applied chronologically with correct effective date logic (lines 198-240)
- Percentage and flat deductions both correct (lines 347-351)
- All monetary arithmetic uses `Decimal` with `ROUND_HALF_UP`

### 6C. Tax Calculator: CORRECT

**File:** `app/services/tax_calculator.py`

Verified correct:

- Federal withholding follows IRS Pub 15-T Percentage Method steps 1-6 (lines 102-170)
- Marginal brackets: each bracket taxes only the portion within its range (lines 173-209)
- Standard deduction subtracted before brackets (lines 117-120)
- W-4 Step 3 credits correctly reduce annual tax (lines 136-154)
- FICA SS wage cap: stops withholding when cumulative wages exceed base (lines 297-303)
- Medicare surtax: additional rate above threshold with cumulative tracking (lines 308-315)
- Input validation prevents negative gross pay and zero pay periods (lines 91-100)
- 2025 and 2026 federal brackets match IRS published values (auth_service.py:47-165)
- 2025-2026 FICA rates and SS wage bases match SSA announcements (auth_service.py:167-183)

### 6D. Recurrence Engine: CORRECT

**File:** `app/services/recurrence_engine.py`

Verified correct:

- All 8 patterns implemented and routed correctly (lines 298-329)
- `monthly`: maps calendar day to containing pay period, handles month-end clamping (lines 332-359)
- `monthly_first`: first period with start_date in each calendar month (lines 362-373)
- `quarterly`: correct modular month calculation from start_month (lines 376-382)
- `semi_annual`: same pattern as quarterly with 6-month intervals (lines 385-391)
- `annual`: correct year-based deduplication (lines 418-438)
- Override protection: `is_override = True` entries never replaced (lines 116-118)
- Soft-delete protection: `is_deleted = True` entries not recreated (lines 121-123)
- Finalized transactions never modified (lines 111-113)
- Duplicate prevention: `_get_existing_map` + unique partial index (Transaction model lines 26-33)
- Cross-user defense-in-depth: template.user_id vs scenario.user_id check (lines 64-70)
- Idempotent: `generate_for_template` called twice produces no duplicates

### 6E. Credit Workflow: CORRECT

**File:** `app/services/credit_workflow.py`

Verified correct:

- Creates exactly one payback transaction (lines 102-113)
- Idempotent: returns existing payback if already credited (lines 60-67)
- Only projected transactions can be marked credit (lines 70-74)
- Payback uses `actual_amount` if set, else `estimated_amount` (line 99)
- Payback in next pay period (line 92)
- Unmark deletes payback transaction (lines 150-157)
- Ownership via `pay_period.user_id` (lines 53-55)
- Linked via `credit_payback_for_id` FK (line 111)

### 6F. Carry Forward: CORRECT (with scenario caveat)

**File:** `app/services/carry_forward_service.py`

Verified correct:

- Only projected, non-deleted transactions carried forward (lines 62-69)
- Template items flagged `is_override = True` after move (lines 77-78)
- Same-period check returns 0 (lines 55-56)
- Ownership verified for both periods (lines 47-53)

**Caveat (B3):** No `scenario_id` filter.

---

## 7. Data Isolation Findings

### Overall Assessment

Most routes correctly verify ownership. The service layer consistently uses `user_id` parameters. Several gaps exist in form-rendering endpoints.

### Queries With Proper Ownership Checks (Verified)

| Blueprint      | Ownership Pattern                                     | Status                    |
| -------------- | ----------------------------------------------------- | ------------------------- |
| `accounts`     | `account.user_id != current_user.id`                  | All 15 endpoints verified |
| `templates`    | `template.user_id != current_user.id`                 | All endpoints verified    |
| `transfers`    | `template.user_id` / `xfer.user_id`                   | All endpoints verified    |
| `salary`       | `profile.user_id != current_user.id`                  | All endpoints verified    |
| `savings`      | `goal.user_id != current_user.id`                     | All endpoints verified    |
| `mortgage`     | `_load_mortgage_account` checks `account.user_id`     | All endpoints verified    |
| `auto_loan`    | `account.user_id != current_user.id`                  | All endpoints verified    |
| `investment`   | `account.user_id != current_user.id`                  | All endpoints verified    |
| `retirement`   | `pension.user_id != current_user.id`                  | All endpoints verified    |
| `categories`   | `category.user_id != current_user.id`                 | All endpoints verified    |
| `settings`     | `acct.user_id == current_user.id`                     | Verified                  |
| `charts`       | `user_id=current_user.id` passed to all service calls | All 6 endpoints verified  |
| `grid`         | User-scoped periods + user-scoped scenario            | Transitively safe         |
| `transactions` | `_get_owned_transaction` checks `pay_period.user_id`  | All mutation endpoints    |

### Queries Missing `user_id` Filter

| File:Line                        | Query                                                          | Risk                                   | Severity |
| -------------------------------- | -------------------------------------------------------------- | -------------------------------------- | -------- |
| `transactions.py:220-222`        | `get_quick_create` loads Category/PayPeriod without user check | Category name + period date disclosure | Medium   |
| `transactions.py:256-257`        | `get_full_create` same pattern                                 | Same                                   | Medium   |
| `transactions.py:294-295`        | `get_empty_cell` same pattern                                  | Same                                   | Medium   |
| `templates.py:419-420`           | `preview_recurrence` loads PayPeriod without user check        | Period structure disclosure            | Medium   |
| `categories.py:93-99`            | Delete in-use check queries without user filter                | Functional bug (blocks deletion)       | Low      |
| `carry_forward_service.py:62-69` | No `scenario_id` filter                                        | Cross-scenario corruption              | Medium   |
| `recurrence_engine.py:264-265`   | `resolve_conflicts` loads by ID only                           | IDOR (but unreachable)                 | Low      |

### Service Layer Isolation: VERIFIED

All services that touch user data correctly use their `user_id` parameter:

- `pay_period_service.py`: All 5 functions filter by `user_id`
- `balance_calculator.py`: Pure function, receives pre-filtered data
- `paycheck_calculator.py`: Pure function, no DB access
- `credit_workflow.py`: Verifies `pay_period.user_id` (lines 53-55, 139-141)
- `carry_forward_service.py`: Verifies period ownership (lines 47-53)
- `recurrence_engine.py`: Cross-user check (lines 64-70)
- `chart_data_service.py`: All queries filter by `user_id`
- `account_resolver.py`: All 4 fallback paths filter by `user_id`

---

## 8. Security Audit Findings

### Authentication: SOLID (except B1)

- Passwords: bcrypt hashing (`auth_service.py:247-266`), no plaintext anywhere
- Validation: 12-char minimum, 72-byte maximum (`auth_service.py:325-328, 366-369`)
- `authenticate()` checks `is_active` (`auth_service.py:303-304`)
- Session invalidation: `session_invalidated_at` vs `_session_created_at` comparison (`__init__.py:64-71`)
- Remember-me: 30 days default, configurable (`config.py:31-33`)
- Session cookies: `SECURE=True`, `HTTPONLY=True`, `SAMESITE=Lax` in ProdConfig (`config.py:93-95`)
- Open redirect prevention: thorough `_is_safe_redirect` (`auth.py:25-66`) -- validates no scheme, no netloc, no backslash prefix, no embedded newlines
- **Gap:** `user_loader` doesn't check `is_active` (B1)

### MFA/TOTP: SOLID

- Secrets encrypted with Fernet at rest (`mfa_service.py:42-51`)
- Backup codes: bcrypt-hashed, single-use (removed after verification at `auth.py:271-273`)
- No bypass possible: pending state in session, user NOT logged in until TOTP verified (`auth.py:92-100`)
- Disable requires both password AND TOTP code (`auth.py:421-442`)
- Rate limited: 5 per 15 minutes on verify (`auth.py:221`)
- TOTP valid_window=1 (30-second clock drift tolerance) (`mfa_service.py:109`)

### CSRF: COMPREHENSIVE

- `CSRFProtect` initialized (`extensions.py:28`) and bound (`__init__.py:46`)
- Meta tag: `<meta name="csrf-token" content="{{ csrf_token() }}">` (`base.html:8`)
- HTMX injection via `htmx:configRequest` event covers ALL methods (`app.js:54-60`)
- All POST forms include `csrf_token()` hidden fields (verified via grep)
- Zero `@csrf.exempt` decorators in codebase

### Input Validation: GOOD

- Marshmallow schemas on all transaction, account, transfer, salary, mortgage, savings routes
- No `| safe` filter on user data in any template
- No `Markup()` on user data
- SQL: all raw queries use bind parameters or hardcoded allowlists; no user input interpolation
- Security headers: CSP, X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy (`__init__.py:286-305`)

### Secrets: CLEAN

- ProdConfig validates SECRET_KEY (rejects "dev-only"), DATABASE_URL, TOTP_ENCRYPTION_KEY (`config.py:97-106`)
- `.env` in `.gitignore` (line 18)
- No hardcoded secrets in application code
- Default credentials only in seed scripts and docs (not in app code)
- No sensitive data in log output

---

## 9. Error Handling and Resilience

### Error Pages: COMPLETE

Custom handlers for 400, 403, 404, 429, 500 (`__init__.py:236-283`). Production mode shows custom pages, not stack traces.

### Transaction Safety: GOOD

- Services use `db.session.flush()` for intermediate steps; routes `commit()` the full operation
- Recurrence regeneration: atomic delete + regenerate (`recurrence_engine.py:233-239`)
- Credit workflow: atomic status change + payback creation
- Carry forward: all moves flushed together
- Salary routes: try/except with `rollback()` on failure (`salary.py:420-428`)

### Entrypoint: MOSTLY RESILIENT

- `set -e`: any failure exits (`entrypoint.sh:2`)
- `DB_PASSWORD` validated before proceeding (lines 14-18)
- Migration/seed failures exit due to `set -e`
- **Gap:** `pg_isready` has no timeout (M2)

---

## 10. Database and Schema

### Migration Chain: UNBROKEN

19 migrations from `9dea99d4e33e` (initial) to `f8f8173ff361` (head). Linear chain, no branches or conflicts. Verified by tracing all `down_revision` links.

### Schema Consistency: GOOD

- All models have corresponding migrations
- FK constraints on all relationship columns
- `user_id` on all user-scoped tables
- Indexes on frequently queried columns (user_id+period_index, period+scenario composites)
- Unique constraints: user+start_date for periods, user+name for accounts
- Check constraints: positive amounts, valid dates

### Reference Data: IDEMPOTENT

`_seed_ref_tables()` in `__init__.py:332-394` checks before insert. `seed_ref_tables.py` follows the same pattern. Multiple runs produce no duplicates.

---

## 11. Configuration and Deployment

### Production Config: CORRECT

- `DEBUG = False` (`config.py:90`)
- Secure cookies (`config.py:93-95`)
- Env var validation with clear errors (`config.py:97-106`)
- `FLASK_ENV: production` in `docker-compose.yml:47`

### Docker: WELL-CONFIGURED

- `python:3.14-slim` (specific version, not `latest`) (`Dockerfile:6`)
- Multi-stage build (builder + runtime)
- Non-root user `shekel` (`Dockerfile:28, 44`)
- `.dockerignore` excludes `.env`, `tests/`, `docs/`, `.github/`
- Nginx reverse proxy with network isolation (`docker-compose.yml:79-104`)
- Health checks on all three services
- `psycopg2==2.9.11` (compiled, not binary -- production-recommended)
- Gunicorn: 2 workers, 120s timeout (`gunicorn.conf.py:24, 30`)

### CI Pipeline: EXISTS

`.github/workflows/ci.yml`: Runs pylint + pytest with PostgreSQL 16 service container on push to main and all PRs. Uses Python 3.14 with `allow-prereleases: true`.

### Operational Status

| Item                 | Status                           | Blocker?     |
| -------------------- | -------------------------------- | ------------ |
| `/health` endpoint   | EXISTS (DB connectivity check)   | N/A          |
| Database backup      | **NOT ON DEV BRANCH**            | **Yes** (B2) |
| Nginx reverse proxy  | EXISTS                           | N/A          |
| CI (lint + test)     | EXISTS                           | N/A          |
| Registration         | EXISTS at `/register`            | N/A          |
| Error pages          | EXISTS (400, 403, 404, 429, 500) | N/A          |
| Structured logging   | EXISTS (JSON, request_id)        | N/A          |
| Security headers     | EXISTS (CSP, HSTS-ready)         | N/A          |
| Docker health checks | EXISTS (all 3 services)          | N/A          |

---

## 12. Testing

### Test Configuration: CORRECT

- Separate database: `TEST_DATABASE_URL` / `postgresql:///shekel_test` (`config.py:65-66`)
- CSRF disabled: `WTF_CSRF_ENABLED = False` (`config.py:68`)
- Rate limiting disabled: `RATELIMIT_ENABLED = False` (`config.py:70`)
- Fast bcrypt: `BCRYPT_LOG_ROUNDS = 4` (`config.py:74`)
- `NullPool` prevents connection leaks (`config.py:81-84`)

### Dependencies: ALL PINNED

All 13 packages in `requirements.txt` pinned to exact versions (e.g., `Flask==3.1.3`). No `>=` or unpinned packages. No unused or missing dependencies detected.

---

## 13. Recommended Production Launch Sequence

### Phase 1: Before Personal Use

1. **Fix `user_loader` is_active check** (B1) -- 1 line in `app/__init__.py`
2. **Set up database backup** (B2) -- `pg_dump` script + cron
3. **Harden seed script password** (B4) -- reject default in production

### Phase 2: Before Sharing with Coworkers

4. **Fix IDOR in form-rendering routes** (H1) -- add ownership checks to 3 transaction endpoints
5. **Fix IDOR in template preview** (H3) -- add ownership check on PayPeriod
6. **Add scenario_id to carry-forward** (B3) -- 1 parameter + filter
7. **Rate limit registration** (H5) -- 1 decorator
8. **Add invite code for registration** (H6) -- prevent unauthorized account creation

### Phase 3: Soon After Launch

9. **Add pg_isready timeout** (M2)
10. **Evaluate balance calculator design** (M1)
11. **Restrict forwarded_allow_ips** (M3)
12. **Use Python 3.13** (M4) -- until 3.14 GA
13. **Sanitize health check errors** (M5)
14. **Scope category delete check** (M6)

### Phase 4: Ongoing

15. **Move `_load_tax_configs` to service layer** (L1) -- fix cross-route import
16. **Phase 8E multi-user audit** -- comprehensive IDOR testing
17. **Add user_id to Transaction model** (H2) -- direct ownership
18. **Audit logging triggers** -- if not already on this branch

---

_End of audit report._
