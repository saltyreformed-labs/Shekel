> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Shekel Production Readiness Audit Report

**Date:** 2026-03-21
**Auditor:** Claude Opus 4.6 (automated code audit)
**Codebase:** Shekel Budget App -- `dev` branch at commit `a23ed30`

---

## 1. Summary

**Overall Assessment: READY FOR PRODUCTION WITH CONDITIONS**

Shekel is a well-engineered personal finance application. The financial calculation core is sound -- Decimal arithmetic is used consistently in all services, rounding is explicit, and the balance calculator is a clean pure function. The security posture is strong: every route checks user_id (zero IDOR vulnerabilities found), CSRF protection covers all forms via Flask-WTF + HTMX header injection, MFA is properly implemented, and session management is robust.

The application has one security vulnerability that must be fixed (open redirect in the login flow), one deployment blocker (external Docker network dependency), and several high-priority items that represent real operational risk. The infrastructure layer (Docker, Nginx, backups, deployment) is substantially more complete than the CLAUDE.md suggests -- most Phase 8 items are actually implemented.

The codebase is clean: zero TODO/FIXME/HACK comments, zero print statements in application code, comprehensive Marshmallow validation on all inputs, and structured JSON logging throughout.

**Conditions for production deployment:**

1. Fix the open redirect vulnerability (B-001)
2. Fix the external Docker network blocker (B-002)
3. Split dev dependencies out of the production image (B-003)

---

## 2. Blockers (Must Fix Before Production)

### B-001 -- Open Redirect in Login Flow

- **Area:** Security (Area 3)
- **File(s):** `app/routes/auth.py:60-61`, `app/routes/auth.py:231-239`
- **Issue:** The `next` parameter from `request.args.get("next")` is passed directly to `redirect()` without validation. An attacker can craft `https://app.example.com/login?next=https://evil.com/phishing` -- after successful login, the user is redirected to the attacker's site.
- **Risk:** Credential phishing. After a user legitimately logs in, they're sent to a lookalike page that says "session expired, please re-enter your password."
- **Fix:** Validate the `next` URL using `urllib.parse.urlparse` to ensure it is a relative path (no scheme, no netloc). Flask does not provide `url_has_allowed_host_and_scheme` natively, so add a helper: `if next_page and urlparse(next_page).netloc: next_page = None`.

### B-002 -- External Docker Network Blocks Fresh Deployment

- **Area:** Docker/Deployment (Area 4)
- **File(s):** `docker-compose.yml:122-124`
- **Issue:** The `monitoring` network is declared as `external: true`. Running `docker compose up` on a fresh host without first creating this network (`docker network create monitoring`) will fail with: `network monitoring declared as external, but could not be found`.
- **Risk:** First deployment fails. A new user following the quickstart instructions cannot start the application.
- **Fix:** Add a `docker network create monitoring 2>/dev/null || true` step to `entrypoint.sh` or `deploy.sh`, OR change the network to not be external with a comment that it should be made external when the monitoring stack is deployed.

### B-003 -- Dev/Test Dependencies in Production Image

- **Area:** Dependencies/Supply Chain (Area 10)
- **File(s):** `requirements.txt:27-40`, `Dockerfile:16`
- **Issue:** `requirements.txt` includes pytest, pytest-cov, pytest-flask, pytest-timeout, factory-boy, pylint, pylint-flask, and pylint-flask-sqlalchemy. The Dockerfile installs all of these into the production image. This adds ~50MB+ of unnecessary packages, increases attack surface, and violates the principle of minimal production images.
- **Risk:** Larger image, slower builds, unnecessary code in production that could be leveraged in a supply chain attack.
- **Fix:** Split into `requirements.txt` (production only) and `requirements-dev.txt` (adds `-r requirements.txt` plus dev/test packages). Update the Dockerfile to only install `requirements.txt`. Update CI to install `requirements-dev.txt`.

---

## 3. High Priority (Should Fix Before Production)

### H-001 -- Missing 400 and 403 Error Handlers

- **Area:** Configuration (Area 6)
- **File(s):** `app/__init__.py:236-255`
- **Issue:** Error handlers exist for 404, 429, and 500 but not for 400 (Bad Request) or 403 (Forbidden). CSRF failures return 400, and permission errors return 403. Without custom handlers, these show the default Werkzeug HTML error page, which leaks framework details in production.
- **Risk:** Information leakage (Werkzeug version, stack details) and poor user experience on CSRF failures.
- **Fix:** Add `@app.errorhandler(400)` and `@app.errorhandler(403)` handlers in `_register_error_handlers()` with corresponding templates in `app/templates/errors/`.

### H-002 -- Test/Production Ref Data Mismatch ("settled" Status)

- **Area:** Test Quality (Area 5)
- **File(s):** `tests/conftest.py:895` vs `scripts/seed_ref_tables.py:42`
- **Issue:** The test conftest seeds Status values as `["projected", "done", "received", "credit", "cancelled"]` -- missing `"settled"`. The production seed script and `app/__init__.py` include `"settled"`. This means tests run against a different reference data set than production.
- **Risk:** Code that references the "settled" status would pass in production but fail in tests (or vice versa). Currently "settled" appears unused in application logic, but the parity gap is a latent risk.
- **Fix:** Add `"settled"` to the Status list in `tests/conftest.py:895`.

### H-003 -- No Seed User Created by Entrypoint

- **Area:** Docker/Deployment (Area 4)
- **File(s):** `entrypoint.sh:22-23`, `docker-compose.yml:54-56`
- **Issue:** The entrypoint runs `seed_ref_tables.py` and `seed_tax_brackets.py` but does NOT run `seed_user.py`. The `SEED_USER_EMAIL`/`SEED_USER_PASSWORD` env vars are passed via docker-compose.yml but never consumed. A fresh `docker compose up` creates a database with reference data but no user -- the registration page is the only way in. Note: `auth_service.register_user()` does seed tax data for new users, so registration works. But the env vars create false confidence that a pre-seeded admin login will exist.
- **Risk:** A user following `.env.example` instructions expects a pre-seeded admin login but gets a blank login page.
- **Fix:** Add `python scripts/seed_user.py` to `entrypoint.sh` after the tax bracket seed step, or document that registration is the intended first-run flow and remove the `SEED_USER_*` env vars from docker-compose.yml.

### H-004 -- `psycopg2-binary` in Production

- **Area:** Dependencies (Area 10)
- **File(s):** `requirements.txt:12`
- **Issue:** `psycopg2-binary` bundles its own `libpq` shared library. The Dockerfile already installs `libpq5` for the runtime. Using the binary package in production is discouraged by the psycopg2 maintainers due to potential version mismatches between the bundled and system libpq.
- **Risk:** Subtle connection issues or SSL/authentication failures if the bundled libpq version diverges from the server.
- **Fix:** Change to `psycopg2==2.9.11` (non-binary). The Dockerfile's builder stage already installs `libpq-dev` and `gcc`, so compilation will succeed.

### H-005 -- Amortization Engine `Decimal(str(payment))` Double Conversion

- **Area:** Financial Correctness (Area 1)
- **File(s):** `app/services/amortization_engine.py:77`
- **Issue:** `Decimal(str(payment))` converts an already-Decimal result to string and back. This is a no-op in most cases but could theoretically lose precision for very large numbers when the string representation truncates significant digits.
- **Risk:** Extremely low probability but the pattern is unnecessary and could mask a real issue if `payment` ever becomes a float through a code change.
- **Fix:** Change `return Decimal(str(payment)).quantize(TWO_PLACES, ROUND_HALF_UP)` to `return payment.quantize(TWO_PLACES, ROUND_HALF_UP)`.

### H-006 -- Two CSRF Tokens Missing from Salary Delete Forms

- **Area:** Security (Area 3)
- **File(s):** `app/templates/salary/_raises_section.html` (delete raise form), `app/templates/salary/_deductions_section.html` (delete deduction form)
- **Issue:** These forms have both `method="POST"` and `hx-post`. The HTMX path works because `app.js` injects the X-CSRFToken header. However, the forms lack a `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">` hidden field. If JavaScript fails to load, the form falls back to a standard POST that Flask-WTF will reject with a 400 CSRF error.
- **Risk:** Not a security vulnerability (Flask-WTF rejects the tokenless POST), but the user gets an unhelpful 400 error page instead of the expected action. Graceful degradation failure.
- **Fix:** Add `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">` to both forms.

---

## 4. Medium Priority (Fix Soon After Launch)

### M-001 -- `float()` Conversions in Chart Data Service

- **Area:** Financial Correctness (Area 1)
- **File(s):** `app/services/chart_data_service.py:307,389,441-442,536-537,691-692,707-708`, `app/routes/auto_loan.py:54`, `app/routes/mortgage.py:64`, `app/routes/retirement.py:319,337`
- **Issue:** Monetary Decimal values are converted to `float()` for Chart.js JSON serialization. These conversions happen at the presentation boundary, not in calculations, so no financial harm occurs. However, the pattern mixes Decimal and float in the same service layer.
- **Risk:** None for correctness (Chart.js requires JSON numbers, not Decimal). Code hygiene concern -- a future developer might copy the `float()` pattern into a calculation.
- **Fix:** Move `float()` conversions to a dedicated serialization step (e.g., a `to_chartjs_data()` helper) or add a comment clarifying these are display-only conversions.

### M-002 -- No `carry_forward_service` Dedicated Tests

- **Area:** Test Coverage (Area 5)
- **File(s):** `app/services/carry_forward_service.py` (no corresponding `tests/test_services/test_carry_forward_service.py`)
- **Issue:** The carry-forward service has no dedicated unit tests. It is tested indirectly via route tests and an idempotency integration test, but edge cases (carry forward to same period, carry forward with mixed statuses, carry forward with template-linked items) are not explicitly covered.
- **Risk:** Regression in a financial operation that moves transactions between periods.
- **Fix:** Create `tests/test_services/test_carry_forward_service.py` with tests for: empty period, all-done period, mixed statuses, template-linked override flagging, same source/target period.

### M-003 -- `DevConfig` Missing `SQLALCHEMY_DATABASE_URI` Fallback

- **Area:** Configuration (Area 6)
- **File(s):** `app/config.py:52`
- **Issue:** `DevConfig.SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")` returns `None` if `DATABASE_URL` is not set. SQLAlchemy will raise an unclear error at connection time rather than a clear configuration error at startup.
- **Risk:** Confusing error message for new developers who forget to set up `.env`.
- **Fix:** Add validation similar to `ProdConfig.__init__()`, or provide a sensible default like `postgresql://localhost/shekel` with a logged warning.

### M-004 -- Seed User Password Below Minimum

- **Area:** Security (Area 3)
- **File(s):** `scripts/seed_user.py:35`, `.env.example:48`
- **Issue:** The default seed password `changeme` is 8 characters. The `change_password` function requires 12+ characters, and `register_user` also requires 12+. However, `seed_user.py` bypasses `register_user()` and calls `hash_password()` directly, which has no minimum length check. A user who keeps the default seed password has a weak password that they cannot "change" to something equally short.
- **Risk:** Weak default password. Not a critical blocker for a single-user app behind Cloudflare Access, but still poor practice.
- **Fix:** Change the default in `.env.example` to a 12+ character password and add a length check to `seed_user.py`.

---

## 5. Low Priority (Technical Debt)

### L-001 -- `MfaConfig` Model Docstring Says "Stub"

- **Area:** Code Quality (Area 8)
- **File(s):** `app/models/user.py:92`
- **Issue:** The docstring reads "Stub table for Phase 6+ MFA/TOTP feature. Schema only -- no logic yet." MFA is fully implemented in Phase 8A.
- **Fix:** Update the docstring.

### L-002 -- `_ensure_schemas` Uses f-string in SQL

- **Area:** Security (Area 3)
- **File(s):** `app/__init__.py:283-284`
- **Issue:** `db.session.execute(db.text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}"))` uses an f-string. The schema names are hardcoded in the same function so this is not exploitable, but the pattern is a bad example.
- **Risk:** None (hardcoded values), but could be copied incorrectly.
- **Fix:** Use parameterized DDL or add a comment noting the values are hardcoded.

### L-003 -- `conftest.py` Schema Drop Uses f-string in SQL

- **Area:** Code Quality (Area 8)
- **File(s):** `tests/conftest.py:107-108`
- **Issue:** Same f-string pattern as L-002, used in test teardown.
- **Fix:** Same as L-002.

### L-004 -- `forwarded_allow_ips = "*"` in Gunicorn Config

- **Area:** Security (Area 3)
- **File(s):** `gunicorn.conf.py:74`
- **Issue:** Trusts `X-Forwarded-*` headers from any IP. In the Docker architecture (Nginx -> Gunicorn on an internal network), this is acceptable because the backend network is not externally accessible. However, if Gunicorn is ever exposed directly, this would allow IP spoofing.
- **Risk:** Low in current architecture. Would become a problem if deployment architecture changes.
- **Fix:** Restrict to the Docker backend network CIDR, or document the constraint.

### L-005 -- Docker Compose `app` Service `DB_PASSWORD` Coupling

- **Area:** Docker/Deployment (Area 4)
- **File(s):** `docker-compose.yml:52`
- **Issue:** The `DB_PASSWORD` env var is set to `${POSTGRES_PASSWORD}` which is correct. But `entrypoint.sh:15` uses `PGPASSWORD="${DB_PASSWORD}"` for the psql command. If someone changes the docker-compose environment block, the entrypoint could silently fail to authenticate.
- **Risk:** Low -- the current config is consistent.
- **Fix:** No action needed, but consider adding a check in entrypoint.sh: `if [ -z "$DB_PASSWORD" ]; then echo "ERROR: DB_PASSWORD not set"; exit 1; fi`.

---

## 6. Checklist of Missing Infrastructure (Area 9)

| Item                                                            | Status       | Location                                                                                                                                                                |
| --------------------------------------------------------------- | ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Nginx reverse proxy configuration                               | **EXISTS**   | `nginx/nginx.conf`                                                                                                                                                      |
| Security headers (X-Content-Type-Options, X-Frame-Options, CSP) | **EXISTS**   | `app/__init__.py:258-277` (Flask) + `nginx/nginx.conf:160` (static files)                                                                                               |
| HSTS                                                            | **N/A**      | Intentionally omitted -- Cloudflare handles TLS termination. Documented in `docs/phase_8d1_implementation_plan.md:191`. Correct decision.                                |
| Cloudflare Tunnel configuration                                 | **EXISTS**   | `cloudflared/config.yml` (template with placeholders)                                                                                                                   |
| Cloudflare Access (zero-trust) configuration                    | **EXTERNAL** | Not in repo (configured in Cloudflare dashboard). Documented in plan docs.                                                                                              |
| Cloudflare WAF rate limiting rules                              | **EXTERNAL** | Not in repo (configured in Cloudflare dashboard).                                                                                                                       |
| Backup script                                                   | **EXISTS**   | `scripts/backup.sh` -- pg_dump, gzip, NAS copy, GPG encryption                                                                                                           |
| Backup retention script                                         | **EXISTS**   | `scripts/backup_retention.sh`                                                                                                                                           |
| Restore script                                                  | **EXISTS**   | `scripts/restore.sh`                                                                                                                                                    |
| Backup verification script                                      | **EXISTS**   | `scripts/verify_backup.sh`                                                                                                                                              |
| Deployment script                                               | **EXISTS**   | `scripts/deploy.sh` -- pull, build, health check, auto-rollback                                                                                                          |
| GitHub Actions CI (lint + test)                                 | **EXISTS**   | `.github/workflows/ci.yml` -- pylint + pytest with PG 16 service                                                                                                         |
| GitHub Actions Docker publish                                   | **EXISTS**   | `.github/workflows/docker-publish.yml` -- GHCR with semver tags                                                                                                          |
| Structured JSON logging                                         | **EXISTS**   | `app/utils/logging_config.py` -- python-json-logger, request_id, duration                                                                                                |
| Audit log (PostgreSQL triggers)                                 | **EXISTS**   | Alembic migration `a8b1c2d3e4f5`, 22 audited tables, trigger function                                                                                                   |
| Gunicorn access logging in JSON                                 | **HANDLED**  | Access logging disabled in Gunicorn (`accesslog = None`); Flask's `after_request` middleware handles structured request logging to avoid duplication. Correct decision. |
| Monitoring (Promtail)                                           | **EXISTS**   | `monitoring/promtail-config.yml` + `monitoring/README.md`                                                                                                               |
| Health endpoint                                                 | **EXISTS**   | `app/routes/health.py` -- `/health` with DB connectivity check                                                                                                           |
| Docker HEALTHCHECK                                              | **EXISTS**   | `Dockerfile:50-51` -- checks `/health` endpoint                                                                                                                          |
| `docker-compose.dev.yml`                                        | **EXISTS**   | Dev compose with separate test-db, live reload                                                                                                                          |
| `.env.example`                                                  | **EXISTS**   | Comprehensive, 128 lines, all vars documented                                                                                                                           |
| `.dockerignore`                                                 | **EXISTS**   | Excludes .env, tests, docs, .git, **pycache**                                                                                                                           |
| Integrity check script                                          | **EXISTS**   | `scripts/integrity_check.py`                                                                                                                                            |
| Audit cleanup script                                            | **EXISTS**   | `scripts/audit_cleanup.py`                                                                                                                                              |

**Summary:** 0 items truly missing. All Phase 8 infrastructure is implemented.

---

## 7. What Looks Good

**Financial Correctness -- Excellent:**

- `Decimal` used consistently across ALL services, models, and schemas. Zero `float` in any calculation path.
- `ROUND_HALF_UP` applied at every monetary quantization point.
- Tax brackets are data-driven with proper marginal rate application.
- FICA includes SS wage base cap with cumulative tracking and Medicare surtax threshold.
- Balance calculator is a clean pure function with no side effects.
- Recurrence engine correctly handles the full state machine: immutable statuses, overrides, deletions, and conflict detection.
- Credit workflow is idempotent (re-marking already-credited returns existing payback).

**Security -- Strong:**

- **Zero IDOR vulnerabilities** across all 18 route modules (123 `@login_required` decorators, every resource fetch verified against `current_user.id`).
- **CSRF coverage** on all 48+ forms via hidden inputs + HTMX header injection.
- **MFA/TOTP** properly implemented with encrypted secret storage (Fernet), bcrypt backup codes, and rate-limited verification.
- **Session invalidation** works correctly -- password changes and manual invalidation both use timestamp comparison.
- **CSP headers** are restrictive and appropriate.
- **ProdConfig validation** rejects insecure SECRET_KEY, missing DATABASE_URL, and missing TOTP_ENCRYPTION_KEY at startup.
- **Bcrypt** with default work factor (12), 72-byte limit enforced, password minimum 12 characters.
- **Rate limiting** on login (5/15min) and MFA verification (5/15min).

**Architecture -- Clean:**

- Service layer is genuinely isolated from Flask -- no request/session imports.
- Application factory pattern with proper extension initialization.
- Defense-in-depth: recurrence engine independently verifies cross-user ownership even though routes already check.
- Test fixtures are well-designed: session-scoped app, per-test truncation, multi-user isolation fixtures.
- Structured logging with request_id correlation, JSON format, and slow-request detection.
- Audit triggers on 22 financial tables with user_id propagation via `SET LOCAL`.

**Infrastructure -- Production-Ready:**

- Multi-stage Dockerfile with non-root user, health check, and build/runtime separation.
- Docker Compose with proper service dependencies, health checks, named volumes, and network isolation.
- Nginx configured correctly: static file serving, gzip, proxy timeouts, real IP from Cloudflare.
- Deploy script with automated rollback on health check failure.
- Backup script with encryption, NAS copy, and retention management.
- CI pipeline runs pylint + full test suite against PostgreSQL 16.

**Database & Migrations -- Solid:**

- Linear chain of 19 migrations with no forks or gaps.
- All migrations have complete downgrade functions (one intentional `pass` for a data-only backfill).
- All NOT NULL column additions include server defaults.
- All raw SQL is wrapped in implicit Alembic transactions.
- 100% FK and index coverage between models and migrations.
- Five PostgreSQL schemas cleanly separate concerns.

**Code Quality -- High:**

- Zero TODO/FIXME/HACK/XXX comments in application code.
- Zero `print()` statements in application code.
- All modules have docstrings.
- Marshmallow validation on every user-facing input.
- No raw SQL injection vectors (the one `db.text("SELECT 1")` in the health check is parameterless).
- Seed scripts are idempotent (check-before-insert pattern).
- 1533 tests across 61 files with multi-user isolation coverage.
