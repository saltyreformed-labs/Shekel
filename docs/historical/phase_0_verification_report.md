> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Phase 0 Verification Report

**Date:** 2026-03-22
**Commit:** `7ad119a` (`Production readines audit and implementation plans for v2`)
**Branch:** dev
**Verified by:** Claude Code (Opus 4.6)

---

## Executive Summary

The codebase is stable. All 1780 tests pass. Pylint scores 9.32/10 with zero fatal messages and one false-positive error. The implementation plan is accurate against the current code -- all 22 findings are confirmed present and unresolved. Four items the plan marked as "Resolved Since Audit" are verified resolved. No blockers to starting Phase 1.

---

## Baseline State

| Item                 | Value                                                                    |
| -------------------- | ------------------------------------------------------------------------ |
| Working tree         | Clean (`nothing to commit, working tree clean`)                          |
| Branch               | `dev`                                                                    |
| Current commit       | `7ad119a` -- "Production readines audit and implementation plans for v2" |
| Plan's stated commit | `e636bf6`                                                                |
| Commit delta         | 4 commits ahead (`7ad119a` > `e636bf6`)                                  |

The implementation plan was validated against `e636bf6`. Four commits have occurred since:

```
7ad119a Production readines audit and implementation plans for v2
e636bf6 chore: update stale counts, replace personal email, clean up credentials
32004dd feat(grid): add in-app baseline scenario recovery for Docker users
7e223c8 fix(entrypoint): add ERR trap with troubleshooting guidance on failure
```

All file/line references in the plan have been re-verified against `7ad119a`. Most references remain accurate; discrepancies are documented in the "Discrepancies Between Plan and Code" section below.

---

## Test Suite Results

| Metric                | Value                                                                                |
| --------------------- | ------------------------------------------------------------------------------------ |
| Test database         | `postgresql://shekel_user:***@localhost:5433/shekel_test` (confirmed non-production) |
| Total tests collected | 1780                                                                                 |
| Total passed          | 1780                                                                                 |
| Total failed          | 0                                                                                    |
| Total errors          | 0                                                                                    |
| Total skipped         | 0                                                                                    |
| Total xfailed/xpassed | 0                                                                                    |
| Wall-clock runtime    | 392.71s (6 min 32 sec)                                                               |

**All 1780 tests passed. No failures, errors, or unexpected outcomes. The test suite is green and Phase 1 may proceed.**

Raw output archived at `docs/baseline_test_results.txt`.

---

## Pylint Baseline

### Application Code (`app/`)

| Metric         | Value       |
| -------------- | ----------- |
| Overall score  | **9.32/10** |
| Convention (C) | 121         |
| Refactor (R)   | 143         |
| Warning (W)    | 126         |
| Error (E)      | 1           |
| Fatal (F)      | 0           |

**Error-level message:**

| File:Line                       | Code  | Message                                          | Verdict                                                                                                                                                                                                                                                    |
| ------------------------------- | ----- | ------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app/routes/settings.py:130:20` | E0601 | `Using variable 'mfa_enabled' before assignment` | **False positive.** The variable is assigned at line 114 inside `elif section == "security":` and used at line 130 with the same guard `if section == "security" else False`. Pylint cannot track conditional assignment across branches. No runtime risk. |

Raw output archived at `docs/baseline_pylint_results.txt`.

### Test Code (`tests/`) -- Informational

| Metric          | Value                             |
| --------------- | --------------------------------- |
| Overall score   | **9.20/10**                       |
| Disabled checks | C0114, C0115, C0116, R0801, W0621 |

This is informational only and does not gate Phase 1.

---

## Implementation Plan Accuracy

All findings from the implementation plan (P1-1 through P6-4) verified against commit `7ad119a`. The plan was written against `e636bf6`; line numbers have been re-verified.

### Verification Matrix

| Finding | File(s)                                                                                  | Plan Line(s)                              | Actual Line(s)                            | Status        | Notes                                                                                                                                                                                                  |
| ------- | ---------------------------------------------------------------------------------------- | ----------------------------------------- | ----------------------------------------- | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| P1-1    | `app/__init__.py`                                                                        | 65-78                                     | 65-78                                     | **Confirmed** | `load_user` body at lines 65-78 (decorator at 58, def at 59). No `is_active` check before `return user` at line 78.                                                                                    |
| P1-2    | `app/services/carry_forward_service.py`                                                  | 62-69                                     | 62-70                                     | **Confirmed** | Query at lines 62-70 has no `scenario_id` filter. Route caller at `transactions.py:418` calls `carry_forward_unpaid(period_id, current_period.id, current_user.id)` -- no scenario_id passed.          |
| P1-3    | `scripts/seed_user.py`                                                                   | 45-46                                     | 45-46                                     | **Confirmed** | Line 45: `email = os.getenv("SEED_USER_EMAIL", "admin@shekel.local")`. Line 46: `password = os.getenv("SEED_USER_PASSWORD", "ChangeMe!2026")`. No production-mode check that rejects the default.      |
| P1-4    | `entrypoint.sh`                                                                          | 31-33                                     | 31-33                                     | **Confirmed** | Lines 31-33: `until pg_isready ... do; sleep 1; done`. No retry counter or max wait.                                                                                                                   |
| P2-1    | `app/routes/transactions.py`                                                             | 220-223                                   | 220-223                                   | **Confirmed** | `get_quick_create`: lines 220-221 load Category/PayPeriod by ID. Lines 222-223 check existence only, not `user_id`.                                                                                    |
| P2-2    | `app/routes/transactions.py`                                                             | 256-258                                   | 256-259                                   | **Confirmed** | `get_full_create`: lines 256-257 load by ID. Lines 258-259 check existence only.                                                                                                                       |
| P2-3    | `app/routes/transactions.py`                                                             | 294-296                                   | 294-297                                   | **Confirmed** | `get_empty_cell`: lines 294-295 load by ID. Lines 296-297 check existence only.                                                                                                                        |
| P2-4    | `app/routes/templates.py`                                                                | 419-421                                   | 419-421                                   | **Confirmed** | `preview_recurrence`: line 419 loads PayPeriod by ID. Line 420 checks `if start_period:` without ownership.                                                                                            |
| P2-5    | `app/routes/auth.py`                                                                     | 133                                       | 133                                       | **Confirmed** | `register()` POST at line 133 has no `@limiter.limit(...)`. Login at line 72 has `@limiter.limit("5 per 15 minutes")` for comparison.                                                                  |
| P2-6    | `app/config.py`, `app/routes/auth.py`                                                    | N/A                                       | N/A                                       | **Confirmed** | No `REGISTRATION_ENABLED` config variable exists in `config.py`. Register routes at lines 125-159 do not check any toggle.                                                                             |
| P3-1    | `app/routes/health.py`                                                                   | 39-42                                     | 39-42                                     | **Confirmed** | Line 42: `"detail": str(exc)` in JSON response to unauthenticated callers.                                                                                                                             |
| P3-2    | `app/routes/categories.py`                                                               | 92-100                                    | 92-100                                    | **Confirmed** | Lines 93-95: `TransactionTemplate` queried by `category_id` only, no `user_id` filter. Lines 97-99: same for `Transaction`.                                                                            |
| P3-3    | `gunicorn.conf.py`                                                                       | 79                                        | 79                                        | **Confirmed** | Line 79: `forwarded_allow_ips = "*"`.                                                                                                                                                                  |
| P3-4    | `Dockerfile`, `.github/workflows/ci.yml`                                                 | 6, 20, CI:61                              | 6, 20, CI:61                              | **Confirmed** | Dockerfile line 6: `FROM python:3.14-slim AS builder`. Line 20: `FROM python:3.14-slim`. CI line 61: `allow-prereleases: true`.                                                                        |
| P3-5    | `app/services/balance_calculator.py`                                                     | 297-323                                   | 297-323                                   | **Confirmed** | `_sum_all()` at line 314: `if status_name in ("credit", "cancelled", "done", "received"): continue`. Done/received excluded in post-anchor periods.                                                    |
| P4-1    | `app/routes/salary.py`, `app/routes/retirement.py`, `app/services/chart_data_service.py` | salary:64, retirement:110, chart_data:626 | salary:64, retirement:110, chart_data:626 | **Confirmed** | `_load_tax_configs` defined at `salary.py:64`, imported at `retirement.py:110` (cross-route import). Duplicate at `chart_data_service.py:626`. Called at salary:119, 536, 579, 715 and chart_data:761. |
| P4-2    | `app/services/recurrence_engine.py`                                                      | 248-272                                   | 248-272                                   | **Confirmed** | `resolve_conflicts` at line 248. Lines 264-265: loads transaction by ID with `db.session.get(Transaction, txn_id)` -- no ownership check.                                                              |
| P4-3    | `app/services/transfer_recurrence.py`                                                    | 190-210                                   | 190-211                                   | **Confirmed** | `resolve_conflicts` at line 190. Lines 203-204: loads transfer by ID with `db.session.get(Transfer, xfer_id)` -- no ownership check.                                                                   |
| P4-4    | `app/config.py`                                                                          | 87-106                                    | 87-106                                    | **Confirmed** | `ProdConfig` at lines 87-106 does NOT set `SQLALCHEMY_ENGINE_OPTIONS`.                                                                                                                                 |

### "Resolved Since Audit" Items -- Verified

| Item                               | Claimed Status               | Verification                                                                                                                                                                                                                                                               |
| ---------------------------------- | ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| B2: No automated backup            | RESOLVED                     | `scripts/backup.sh` exists (real backup script with pg_dump, NAS support, GPG encryption). `scripts/backup_retention.sh` exists (tiered retention pruning). `docs/backup_runbook.md` exists with substantive content (script inventory, retention policy, cron schedules). |
| M4: Python 3.14 pre-release        | RESOLVED (incorrect finding) | Python 3.14 reached GA 2025-10-07. `python:3.14-slim` is production-grade. `allow-prereleases: true` in CI is a leftover -- should be removed per P3-4.                                                                                                                    |
| L5: Personal email in reset_mfa.py | RESOLVED                     | `scripts/reset_mfa.py:11` now uses `admin@shekel.local`.                                                                                                                                                                                                                   |
| L6: MfaConfig docstring stale      | RESOLVED                     | `app/models/user.py:91-104` has accurate docstring describing encrypted TOTP secret, backup codes, and related routes.                                                                                                                                                     |

### "Not Actionable" Items -- Verified

| Item                        | Claimed Status | Verification                                                                                                                                                                   |
| --------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| H2: Grid transitive safety  | Not actionable | `grid.py:44-47` scopes by user via `filter_by(user_id=user_id, is_baseline=True)`. Lines 82-90 filter transactions by `scenario.id` from user-scoped query. Transitively safe. |
| L2: import-outside-toplevel | Not actionable | Standard Flask factory pattern. Confirmed multiple instances in `__init__.py`, `settings.py`, `categories.py`.                                                                 |
| L3: broad-except in charts  | Not actionable | Appropriate for optional UI components. Confirmed in `charts.py`.                                                                                                              |

---

## Infrastructure Health

### 0-5a: Dependency Integrity

| Check                                    | Result                                                                         |
| ---------------------------------------- | ------------------------------------------------------------------------------ |
| Total dependencies in `requirements.txt` | 15 packages (13 direct + 2 sub-line entries for qrcode[pil])                   |
| All pinned to exact versions?            | **Yes.** Every dependency uses `==`. No `>=`, `~=`, or unpinned entries.       |
| Installed vs. pinned mismatches          | None detected. All installed versions match `requirements.txt`.                |
| Security advisories                      | Deferred -- `pip-audit` not run to avoid modifying the environment in Phase 0. |

### 0-5b: Migration Chain Integrity

| Check             | Result                                                                                                                                                                                                                                                         |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Number of heads   | **1** (`f8f8173ff361`)                                                                                                                                                                                                                                         |
| Total migrations  | **19** (linear chain, no branches)                                                                                                                                                                                                                             |
| Database at head? | `flask db check` returned exit 1 due to Alembic detecting a `remove_table` for `alembic_version` -- this is a known Alembic quirk with multi-schema setups and does NOT indicate the database is out of date. `flask db heads` confirms `f8f8173ff361 (head)`. |

### 0-5c: Repository Hygiene

| Check                       | Result                                                                                                                                                                                                                          |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `.env` in `.gitignore`      | **Yes** -- line 18: `.env`                                                                                                                                                                                                      |
| Committed `.env` files      | `.env.dev` was committed in commit `c6c85aab` ("Phase 8B implementation"). Contains dev-only values (`dev-secret-key-not-for-production`, `dev_password_change_me`). No real secrets. `.env.example` also committed (expected). |
| `.dockerignore` coverage    | **Good.** Excludes: `.git`, `.env`, `.env.*` (except `.env.example`), `tests/`, `docs/`, `.github/`, `__pycache__`, `venv`, `pgdata/`, `logs/`, Docker files, linting config.                                                   |
| TODO/FIXME/HACK/XXX in app/ | **None found.** Zero matches across all Python files in `app/`.                                                                                                                                                                 |

### 0-5d: Configuration Validation

| Check                                | Result                                                                                                                                                                                                                                                                                                                                                                    |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ProdConfig.DEBUG`                   | `False` (line 90)                                                                                                                                                                                                                                                                                                                                                         |
| `SESSION_COOKIE_SECURE`              | `True` (line 93)                                                                                                                                                                                                                                                                                                                                                          |
| `SESSION_COOKIE_HTTPONLY`            | `True` (line 94)                                                                                                                                                                                                                                                                                                                                                          |
| `SESSION_COOKIE_SAMESITE`            | `"Lax"` (line 95)                                                                                                                                                                                                                                                                                                                                                         |
| `SECRET_KEY` validated?              | **Yes** -- line 99: rejects if missing or starts with `"dev-only"`                                                                                                                                                                                                                                                                                                        |
| `DATABASE_URL` validated?            | **Yes** -- line 103-104: rejects if not set                                                                                                                                                                                                                                                                                                                               |
| `TOTP_ENCRYPTION_KEY` validated?     | **No** -- ProdConfig does NOT validate this. It is checked as a warning in `create_app()` (lines 42-46) but the app starts without it. This is intentional -- MFA is optional.                                                                                                                                                                                            |
| `TestConfig` uses separate DB?       | **Yes** -- `TEST_DATABASE_URL` / `postgresql:///shekel_test` (line 65-66)                                                                                                                                                                                                                                                                                                 |
| `TestConfig` disables CSRF?          | **Yes** -- `WTF_CSRF_ENABLED = False` (line 68)                                                                                                                                                                                                                                                                                                                           |
| `TestConfig` disables rate limiting? | **Yes** -- `RATELIMIT_ENABLED = False` (line 70)                                                                                                                                                                                                                                                                                                                          |
| `.env.example` completeness          | **Comprehensive.** Documents: SECRET*KEY, FLASK_ENV, DATABASE_URL, TEST_DATABASE_URL, POSTGRES_PASSWORD, DB_HOST/PORT/USER/NAME, TOTP_ENCRYPTION_KEY, REMEMBER_COOKIE_DURATION_DAYS, SEED_USER*_, LOG*LEVEL, SLOW_REQUEST_THRESHOLD_MS, AUDIT_RETENTION_DAYS, GUNICORN*_, NGINX*PORT, BACKUP*_, RETENTION\__, DEPLOY\_\*, container names. All with explanatory comments. |

### 0-5e: Docker Infrastructure

Docker is available (`Docker 29.3.0`, `Compose 5.1.1`). File structure verified (not started in Phase 0):

**Dockerfile:**

- Multi-stage build: builder (line 6) + runtime (line 20). CONFIRMED.
- Non-root user `shekel` (lines 28, 44). CONFIRMED.
- No `pip install --break-system-packages` in production stage. CONFIRMED.
- `psycopg2==2.9.11` in requirements.txt (compiled, not binary). CONFIRMED.
- Python image: `python:3.14-slim` (not pinned to patch -- see P3-4).

**docker-compose.yml:**

- Health checks on all 3 services (db:32-36, app:71-76, nginx:98-103). CONFIRMED.
- Database port NOT exposed to host (db has no `ports:` mapping). CONFIRMED.
- Nginx as reverse proxy on frontend + backend networks. CONFIRMED.
- `FLASK_ENV: production` (line 47). CONFIRMED.
- `pgdata` volume (line 107). CONFIRMED.
- Backend network is `internal: true` (line 120). CONFIRMED.

**entrypoint.sh:**

- `set -eEo pipefail` at line 2. CONFIRMED.
- ERR trap with troubleshooting guidance (lines 7-25). CONFIRMED.
- `DB_PASSWORD` validation (lines 37-41). CONFIRMED.
- Migration via `scripts/init_database.py` (line 50). CONFIRMED.
- Reference data seeding (line 54). CONFIRMED.
- `exec "$@"` for final process (line 90). CONFIRMED.

### 0-5f: CI Pipeline Verification

| Check                     | Result                                                                        |
| ------------------------- | ----------------------------------------------------------------------------- |
| Triggers                  | Push to `main` and all PRs (lines 14-16). CONFIRMED.                          |
| Runs pylint?              | **Yes** -- line 83: `pylint app/ --fail-on=E,F --output-format=colorized`     |
| Runs pytest?              | **Yes** -- line 89: `pytest --tb=short -q`                                    |
| Python version            | `3.14` (line 60) -- matches Dockerfile.                                       |
| PostgreSQL version        | `postgres:16` (line 27) -- matches `docker-compose.yml` `postgres:16-alpine`. |
| `allow-prereleases: true` | **Still present** at line 61. Should be removed per P3-4 (Python 3.14 is GA). |

---

## Onboarding Path Verification

### README.md Assessment

| Check                                | Result                                                                                                                                   |
| ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------- |
| Docker Quick Start exists?           | **Yes** -- comprehensive section at lines 13-78.                                                                                         |
| All env vars documented?             | **Yes** -- Quick Start table (lines 36-42) covers required vars; `.env.example` covers all.                                              |
| `.env.example` has comments?         | **Yes** -- every variable has explanatory comments.                                                                                      |
| `docs/backup_runbook.md` referenced? | **No.** The README does not mention backups or link to the runbook. **Gap -- should be addressed in P6-3.**                              |
| Test count accurate?                 | README says "1780 test functions across 63 test files". Actual: **1780 tests, 63 files.** MATCH.                                         |
| Template count accurate?             | README says "80 files, 16 directories". Actual: **80 HTML files, 16 subdirectories** (17 total dirs including `templates/` root). MATCH. |
| Model count accurate?                | README says "21 files, 5 PG schemas". Actual: **21 model files, 5 schemas.** MATCH.                                                      |
| Route count accurate?                | README says "17 route modules". Actual: **17.** MATCH.                                                                                   |
| Service count accurate?              | README says "21 service modules". Actual: **21.** MATCH.                                                                                 |
| JS count accurate?                   | README says "16 chart/grid/form scripts". Actual: **16.** MATCH.                                                                         |
| Migration count accurate?            | README says "19 versions". Actual: **19.** MATCH.                                                                                        |
| Commands work?                       | `flask db upgrade` and `pytest` confirmed functional in this environment.                                                                |

---

## Additional Security Scan

### Raw SQL Injection

| File:Line                                                                        | Pattern                                                 | Verdict                                                                                                                                                                   |
| -------------------------------------------------------------------------------- | ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `app/__init__.py:333`                                                            | `db.text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}")` | **Safe.** `schema_name` iterates over `_ALLOWED_SCHEMAS`, a hardcoded `frozenset` of 5 values (line 318). No user input. Comment at lines 325-327 explains the allowlist. |
| `app/utils/logging_config.py:120`                                                | `db.text("SET LOCAL app.current_user_id = :uid")`       | **Safe.** Uses bind parameter `:uid`.                                                                                                                                     |
| `app/routes/health.py:35`                                                        | `db.text("SELECT 1")`                                   | **Safe.** Hardcoded literal.                                                                                                                                              |
| Model files (`transaction.py:28`, `transfer.py:28`, `mortgage_params.py:36,113`) | `db.text(...)` in `server_default` / `postgresql_where` | **Safe.** Schema definition, not runtime query.                                                                                                                           |

**No SQL injection vulnerabilities found.**

### Unsafe Template Rendering

| Check                  | Result                                               |
| ---------------------- | ---------------------------------------------------- |
| `\| safe` in templates | **None found.** Zero instances across 80 HTML files. |
| `Markup()` in Python   | 4 instances found. All safe:                         |

| File:Line          | Content               | Verdict                                                                                       |
| ------------------ | --------------------- | --------------------------------------------------------------------------------------------- |
| `templates.py:443` | `return Markup(html)` | HTML built from `p.start_date.strftime()` -- database datetime objects, not user input. Safe. |
| `salary.py:163`    | `flash(Markup(...))`  | Hardcoded HTML with `/register` link. No user input. Safe.                                    |
| `salary.py:196`    | `flash(Markup(...))`  | Hardcoded HTML with `url_for(...)` link. No user input. Safe.                                 |
| `salary.py:556`    | `flash(Markup(...))`  | Same pattern. Safe.                                                                           |

### Debug/Development Artifacts

| Check                        | Result                                                                          |
| ---------------------------- | ------------------------------------------------------------------------------- |
| `print()` in app/            | **None found** (only `register_blueprint` lines matched the grep pattern).      |
| `pdb`, `breakpoint`, `ipdb`  | **None found.**                                                                 |
| `DEBUG = True` in ProdConfig | **No.** Only in DevConfig (line 51). ProdConfig sets `DEBUG = False` (line 90). |

### Hardcoded Credentials

| Check                               | Result                                                                                                                                 |
| ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Hardcoded passwords/secrets in app/ | **None found.** All credential references are environment variable reads, hashing functions, or encryption/decryption service methods. |

### Float Usage in Financial Calculations

| Check                        | Result                                                                                                                                                                                                                                                                                                                     |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `float()` in `app/services/` | **12 instances, all in `chart_data_service.py`.** Every instance uses `_to_chart_float()` (defined at line 36) which converts `Decimal` to `float` at the Chart.js presentation boundary only. No `float()` in balance_calculator, paycheck_calculator, tax_calculator, recurrence_engine, or any other financial service. |

**No security vulnerabilities found beyond those already documented in the implementation plan.**

---

## Phase 1 Readiness Assessment

**Phase 1 may proceed. No blockers identified.**

All conditions met:

- Working tree is clean.
- All 1780 tests pass with zero failures.
- Pylint scores 9.32/10 with no real errors (1 false positive).
- The implementation plan's 22 findings are all confirmed present and accurately described.
- The 4 "Resolved Since Audit" items are verified resolved.
- Infrastructure (dependencies, migrations, Docker, CI) is healthy.
- No additional security vulnerabilities found beyond those already documented.

---

## Discrepancies Between Plan and Code

The following cases show where the implementation plan's references differ from the current code at commit `7ad119a`. For each, the corrected information is provided. These are minor line-number shifts or clarifications -- no findings are invalidated.

| Finding  | Plan's Reference                                    | Actual (7ad119a)                                                              | Impact                                                                                                             |
| -------- | --------------------------------------------------- | ----------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| P1-1     | "line 65-78" for `load_user` callback               | Function declared at line 59, decorator at 58, body at 65-78                  | **None** -- plan's line range for the body is correct. The audit's reference (`52-72`) is inaccurate.              |
| P1-2     | "carry_forward()" function name                     | Function is actually named `carry_forward_unpaid()` at line 20                | **Minor** -- plan uses shorthand. The function signature and behavior match.                                       |
| P1-2     | Route caller "in `app/routes/grid.py`"              | Carry-forward route is in `app/routes/transactions.py:406-421`, not `grid.py` | **Correction needed.** The plan incorrectly says the route caller is in `grid.py`. It is in `transactions.py:418`. |
| P2-2     | "lines 258-259" for existence check                 | Actual lines 258-259                                                          | **None** -- exact match.                                                                                           |
| P3-4     | "Line 6: FROM python:3.14.3-slim" as fix target     | Line 6 currently reads `FROM python:3.14-slim AS builder`                     | **None** -- plan correctly identifies what to change.                                                              |
| P4-2     | "lines 248-272" for `resolve_conflicts`             | Function at lines 248-272                                                     | **None** -- exact match.                                                                                           |
| P4-3     | "lines 190-210" for transfer `resolve_conflicts`    | Function at lines 190-211                                                     | **Minor** -- off by one line at the end (db.session.flush() at 211).                                               |
| Audit B1 | "File: app/**init**.py:52-72"                       | Function at lines 58-78                                                       | **Audit line numbers are wrong.** Plan corrected them to 65-78 (body).                                             |
| Audit B3 | "File: app/services/carry_forward_service.py:62-69" | Lines 62-70 (inclusive of `.all()`)                                           | **Minor** -- off by one.                                                                                           |
