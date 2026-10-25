> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Shekel Phase 8: Hardening & Ops Plan

## Overview

Phase 8 transforms Shekel from a working development app on a local network into a production-ready, externally accessible personal finance application with security hardening, observability, automated backups, and minimal multi-user groundwork.

**Goal:** Production-ready for daily single-user use with external access via Cloudflare Tunnel, plus registration and user_id isolation verified so multi-user can be enabled later without retrofitting.

**Deferred to later phases:** CSV export, mobile-responsive layout refinement. These were evaluated and intentionally excluded to keep Phase 8 focused on security, observability, reliability, and deployment.

**Priority order (as ranked):**

1. MFA and security hardening
2. Audit and structured logging
3. Backups and disaster recovery
4. Production deployment automation
5. Multi-user groundwork (minimal)

---

## Sub-Phase Structure

Phase 8 is organized into five sub-phases. Unlike the UI/UX remediation plan, these sub-phases have stronger dependencies and should generally be completed in order, though 8C (backups) and 8E (multi-user groundwork) are independent of each other and can be done in either order after 8B.

```
8A: Security Hardening
 |
 v
8B: Audit & Structured Logging
 |
 +---------+---------+
 |                   |
 v                   v
8C: Backups &      8E: Multi-User
    Disaster            Groundwork
    Recovery
 |
 v
8D: Production Deployment
```

**Critical path:** 8A -> 8B -> 8C -> 8D

**Independent after 8B:** 8E can be done in parallel with 8C and 8D.

---

## Sub-Phase 8A: Security Hardening

### Scope

CSRF audit, password management, MFA/TOTP with backup codes, app-level rate limiting, session management improvements, and custom error pages.

### Depends On

UI/UX remediation plan Phase 4 (navbar restructure) should be complete so that new security-related UI elements (password change, MFA setup) integrate into the finalized navigation and settings structure.

### Risk Level

**Medium.** MFA changes the login flow, which is exercised by every authenticated test. The two-step login flow must be carefully integrated so tests can still authenticate without TOTP when MFA is not enabled. CSRF audit could surface missing tokens that would break form submissions.

### Changes

#### CSRF Audit and Remediation

1. **Audit all forms for CSRF protection.** Run a grep across all `.html` templates to identify forms missing `csrf_token()`. Every `<form>` element that submits via POST must include a CSRF token.
   - Deliverable: a checklist of every form in the app, its template file, and whether `csrf_token()` is present
   - Any missing tokens are added in the same commit

2. **Verify CSRF on HTMX requests.** HTMX sends requests that bypass traditional form submission. Confirm that the HTMX configuration includes the CSRF token in request headers. If not, add it via `hx-headers` or a global HTMX config in `base.html`:

   ```html
   <body hx-headers='{"X-CSRFToken": "{{ csrf_token() }}"}'></body>
   ```

3. **Verify Flask-WTF or equivalent middleware rejects requests without valid tokens.** Confirm that `CSRFProtect` is initialized in the app factory or that an equivalent mechanism is in place.

#### Password Change Flow

4. **Add a password change page.** Accessible from the settings dashboard (add a "Security" section to the existing settings sidebar from UI/UX remediation Phase 3).
   - `app/templates/settings/_security.html`: new partial template with password change form
   - Fields: current password, new password, confirm new password
   - Validation: current password must match, new password must meet minimum length (12 characters recommended), new and confirm must match
   - On success: flash confirmation, invalidate all other sessions for the user (see session management below)
   - On failure: flash specific error (wrong current password vs. mismatch vs. too short)

5. **Add password change route.**
   - `app/routes/auth.py` or `app/routes/settings.py`: POST endpoint for password change
   - Service layer handles bcrypt hashing
   - Logs the password change event (auth event logging)

#### MFA / TOTP

6. **Implement TOTP setup flow.** The `auth.mfa_configs` table stub already exists. Build the feature on top of it.
   - **Setup page** (`app/templates/settings/_mfa_setup.html`): accessible from the Security section of settings
   - Generate a TOTP secret (pyotp library), display as QR code (qrcode library) and manual entry key
   - User enters a confirmation code from their authenticator app to verify setup
   - On confirmation: store encrypted secret, set `is_enabled = True`, set `confirmed_at`
   - Display backup codes (see below) only once after successful setup

7. **Generate and store backup codes.**
   - Generate 10 single-use backup codes (8-character alphanumeric, cryptographically random)
   - Store as bcrypt hashes in `auth.mfa_configs.backup_codes` (JSONB array of hashes)
   - Display the plaintext codes to the user exactly once after MFA setup, with a clear warning to save them
   - Each code can only be used once; on use, remove its hash from the array
   - Provide a "Regenerate backup codes" button that generates a new set (invalidates old ones)

8. **Modify the login flow to support two-step authentication.**
   - Step 1: email + password (existing flow, unchanged)
   - Step 2 (only if MFA is enabled): redirect to a TOTP verification page after successful password check
   - `app/templates/auth/mfa_verify.html`: simple form with a single input for the 6-digit code, plus a "Use backup code" link
   - On successful TOTP or backup code: complete the login, create the session
   - On failure: allow retry, do not reveal whether the code was wrong or expired
   - The session is not created until both steps pass; no partial auth state

9. **MFA disable flow.**
   - Require current password + valid TOTP code to disable MFA
   - Accessible from the Security settings section
   - Clears the TOTP secret and backup codes from the database

10. **MFA recovery via SSH reset script.**
    - `scripts/reset_mfa.py`: CLI script that disables MFA for a given user email
    - Requires direct database access (run on the server)
    - This is the fallback if backup codes are exhausted and the TOTP device is lost
    - Documented in the runbook

#### App-Level Rate Limiting

11. **Add rate limiting middleware on the login endpoint.**
    - Use Flask-Limiter or a lightweight custom middleware
    - Limit: 5 failed login attempts per IP per 15-minute window (configurable)
    - On limit exceeded: return 429 Too Many Requests with a user-friendly message and a retry-after header
    - Rate limit state: in-memory (dictionary with TTL) is sufficient for single-instance deployment; no need for Redis
    - Log rate limit events as security events

12. **Rate limit the MFA verification endpoint** with the same or stricter limits (5 attempts per 15 minutes).

#### Session Management

13. **Add "Log out all sessions" functionality.**
    - Add a button to the Security settings section
    - Implementation: store a `session_invalidated_at` timestamp on the user record; the session validation check compares the session creation time against this timestamp
    - On password change: automatically invalidate all other sessions

14. **Session cleanup.** If using server-side sessions stored in the database or filesystem, add a cleanup mechanism for expired sessions.
    - If using Flask's default cookie-based sessions: no cleanup needed (stateless)
    - If using Flask-Session with server-side storage: add a periodic cleanup task (cron or app startup)

#### Error Handling

15. **Custom error pages.** Create user-friendly templates for common HTTP errors so production users never see a stack trace.
    - `app/templates/errors/404.html`: "Page not found" with navigation back to the dashboard
    - `app/templates/errors/500.html`: "Something went wrong" with a message to try again
    - `app/templates/errors/429.html`: "Too many requests" with retry guidance
    - Register error handlers in the app factory

16. **Suppress stack traces in production.** Ensure `DEBUG = False` in the production config and that Flask's error handler returns the custom templates.

### Test Gate

- [ ] `pytest` passes (all existing tests)
- [ ] Every form in the app includes a CSRF token (verified by audit checklist)
- [ ] HTMX requests include CSRF header
- [ ] Password change works: correct current password required, new password hashed, sessions invalidated
- [ ] MFA setup: QR code displayed, confirmation code validates, backup codes generated
- [ ] MFA login: two-step flow works, backup code works, failed code rejected
- [ ] MFA disable: requires password + TOTP
- [ ] Rate limiting: 6th failed login attempt within 15 minutes returns 429
- [ ] Custom error pages render for 404, 500, 429
- [ ] Manual test: reset_mfa.py script successfully disables MFA for a user

---

## Sub-Phase 8B: Audit & Structured Logging

### Scope

PostgreSQL trigger-based audit logging on financial tables, structured JSON application logging with request_id correlation and request duration tracking, and log shipping configuration for Grafana/Loki.

### Depends On

8A (security events from MFA, password changes, and rate limiting should be captured by the structured logging system).

### Risk Level

**Low.** Audit triggers are additive (no existing logic changes). Structured logging replaces console output but does not change application behavior. The main risk is trigger overhead on write-heavy operations (recurrence engine regeneration), which should be tested.

### Changes

#### PostgreSQL Audit Logging

1. **Create the `system.audit_log` table.**

   ```sql
   CREATE TABLE system.audit_log (
       id BIGSERIAL PRIMARY KEY,
       table_schema VARCHAR(50) NOT NULL,
       table_name VARCHAR(100) NOT NULL,
       operation VARCHAR(10) NOT NULL,  -- INSERT, UPDATE, DELETE
       row_id INTEGER,
       old_data JSONB,
       new_data JSONB,
       changed_fields TEXT[],           -- UPDATE only: list of columns that changed
       user_id INTEGER,                 -- application user, if available
       db_user VARCHAR(100) DEFAULT current_user,
       executed_at TIMESTAMPTZ DEFAULT NOW()
   );
   CREATE INDEX idx_audit_log_table ON system.audit_log(table_schema, table_name);
   CREATE INDEX idx_audit_log_executed ON system.audit_log(executed_at);
   CREATE INDEX idx_audit_log_row ON system.audit_log(table_name, row_id);
   ```

2. **Create a generic audit trigger function.** A single PL/pgSQL function that handles INSERT, UPDATE, and DELETE for any table. For UPDATE, it records only the fields that actually changed (comparing OLD and NEW).
   - The function reads `current_setting('app.current_user_id', true)` to capture the application-level user_id (set by Flask middleware on each request).

3. **Attach triggers to financial tables.** Priority tables that hold data with financial significance:
   - `budget.accounts`
   - `budget.transactions`
   - `budget.transaction_templates`
   - `budget.transfers`
   - `budget.savings_goals`
   - `budget.recurrence_rules`
   - `budget.pay_periods`
   - `budget.account_anchor_history`
   - `budget.hysa_params`
   - `budget.mortgage_params`
   - `budget.mortgage_rate_history`
   - `budget.mortgage_escrow_components`
   - `budget.auto_loan_params`
   - `budget.investment_params`
   - `salary.salary_profiles`
   - `salary.salary_raises`
   - `salary.paycheck_deductions`
   - `salary.pension_profiles`
   - `auth.users` (for login, password change, MFA events)
   - `auth.user_settings`
   - `auth.mfa_configs`

   Tables excluded: `ref.*` (lookup data rarely changes and is not user-specific), `salary.tax_bracket_sets`, `salary.tax_brackets`, `salary.fica_configs`, `salary.state_tax_configs` (reference tax data, not user financial actions). These can be added later if needed.

4. **Flask middleware to set `app.current_user_id`.** On each request, if the user is authenticated, execute `SET LOCAL app.current_user_id = '<user_id>'` so the trigger function can capture who made the change.

5. **Alembic migration** for the audit_log table and trigger attachments. The trigger function and all ATTACH statements go in a single migration.

6. **Retention policy.** A SQL function or script that deletes audit_log rows older than a configurable retention period. Default: 365 days.
   - `scripts/audit_cleanup.py`: CLI script that runs the retention purge
   - Intended to be called by cron (daily)
   - Documented in the runbook

7. **Performance testing.** Run the recurrence engine regeneration for a full 2-year horizon and measure the time with and without triggers attached. If overhead is significant (more than 20% slower), consider disabling triggers during bulk regeneration with `ALTER TABLE ... DISABLE TRIGGER` in a transaction, then re-enable.

#### Structured Application Logging

8. **Replace basic logging with structured JSON logging.** Use `python-json-logger` or equivalent.
   - Configure in the app factory based on the environment (JSON for production, human-readable for development)
   - Standard fields on every log entry: `timestamp`, `level`, `logger`, `message`, `request_id`, `user_id`, `remote_addr`

9. **Add request_id middleware.** Flask before_request hook that generates a UUID for each request and stores it in Flask's `g` object. The structured logger includes this in every log entry. Also return the request_id in a response header (`X-Request-Id`) for debugging.

10. **Add request duration tracking.** Flask after_request hook that calculates and logs the request duration in milliseconds. Log at INFO level for requests over a configurable threshold (default 500ms), DEBUG otherwise.

11. **Define log event categories.** Standardize the events that get logged:
    - **Auth events** (INFO): login success, login failure, logout, password change, MFA setup, MFA verification, MFA disable, rate limit hit
    - **Business events** (INFO): transaction create/update/delete, template create/update, transfer create, recurrence regeneration, anchor balance update, carry forward
    - **Error events** (ERROR): unhandled exceptions, validation failures, database errors
    - **Performance events** (WARNING): slow requests (over threshold)

12. **Configure log output for Grafana/Loki.** Write JSON logs to stdout (Docker best practice). Loki can scrape container logs directly via Promtail or Docker log driver. Document the Promtail configuration needed on the Proxmox host.
    - Deliverable: a sample `promtail-config.yml` that scrapes the Shekel container logs
    - Deliverable: document the Grafana/Loki setup steps in the runbook (Loki and Grafana are assumed to run as separate containers on the Proxmox host)

### Test Gate

- [ ] `pytest` passes (all existing tests)
- [ ] Audit triggers fire: INSERT/UPDATE/DELETE on `budget.transactions` produce rows in `system.audit_log`
- [ ] Audit log captures changed_fields on UPDATE (only the columns that changed)
- [ ] Audit log captures user_id from the application context
- [ ] `scripts/audit_cleanup.py` deletes rows older than the configured retention period
- [ ] Application logs output structured JSON in production config
- [ ] Every log entry includes request_id, timestamp, level
- [ ] Request duration is logged for every request
- [ ] Auth events (login, logout, password change) appear in logs
- [ ] Recurrence engine regeneration with triggers: overhead is acceptable (less than 20% slower)

---

## Sub-Phase 8C: Backups & Disaster Recovery

### Scope

Automated `pg_dump` backups with tiered retention, NAS copy, restore scripts, and backup verification.

### Depends On

8B (structured logging is used to log backup success/failure events). Can proceed in parallel with 8E.

### Risk Level

**Low.** Backup scripts are additive and do not touch the application code. The main risk is the NAS mount configuration, which is environment-specific.

### Changes

#### Automated Backup Script

1. **Create the backup script.** `scripts/backup.sh`: a shell script that performs a `pg_dump`, compresses the output, timestamps the filename, and copies to both local and NAS destinations.
   - Output format: `shekel_backup_YYYYMMDD_HHMMSS.sql.gz`
   - Local destination: `/var/backups/shekel/` (configurable)
   - NAS destination: a mounted network share (path configurable, e.g., `/mnt/nas/backups/shekel/`)
   - Exit code: 0 on success, non-zero on failure
   - Logs success/failure to stdout (picked up by structured logging if run within the container, or by cron's mail if run on the host)

2. **Retention policy script.** `scripts/backup_retention.sh`: prunes old backups according to tiered retention.
   - Daily backups: keep for 7 days
   - Weekly backups (Sunday): keep for 4 weeks
   - Monthly backups (1st of month): keep for 6 months
   - Applies to both local and NAS destinations independently
   - Logs which files were pruned

3. **Cron configuration.** Document the crontab entries for the Proxmox host (or inside the container if preferred):
   - Daily backup: `0 2 * * * /path/to/backup.sh` (2:00 AM)
   - Daily retention cleanup: `30 2 * * * /path/to/backup_retention.sh` (2:30 AM)

4. **NAS mount documentation.** Document how to configure the NAS mount point on the Proxmox host (NFS or CIFS/SMB, depending on the NAS). Include fstab entry and a verification step.

#### Restore Procedure

5. **Create the restore script.** `scripts/restore.sh`: takes a backup file path as an argument, stops the app, drops and recreates the database, restores from the backup, runs any pending Alembic migrations (in case the backup is from a slightly older schema version), and restarts the app.
   - Interactive confirmation prompt before proceeding ("This will replace all data. Continue? [y/N]")
   - Logs each step
   - Tested with a known-good backup

6. **Document the restore procedure in the runbook.** Step-by-step instructions including:
   - How to identify which backup to restore from
   - How to restore to a specific date
   - How to verify the restore was successful
   - How to restore from the NAS if local backups are unavailable

#### Backup Verification

7. **Create a backup verification script.** `scripts/verify_backup.sh`: restores a backup to a temporary database, runs a set of sanity check queries (row counts on key tables, checks for the expected user, checks that pay_periods span a reasonable date range), and drops the temporary database.
   - Can be run manually or on a schedule (weekly recommended)
   - Logs pass/fail for each check

8. **Document the verification schedule** in the runbook. Recommend weekly manual or automated verification.

### Test Gate

- [ ] `backup.sh` produces a valid compressed dump file in local and NAS destinations
- [ ] `backup_retention.sh` correctly prunes files according to the tiered policy
- [ ] `restore.sh` successfully restores a backup to a clean database
- [ ] Application starts and functions correctly after a restore
- [ ] `verify_backup.sh` passes all sanity checks on a known-good backup
- [ ] Backup failure (e.g., NAS unreachable) produces a non-zero exit code and logs an error

---

## Sub-Phase 8D: Production Deployment

### Scope

Finalize Docker containerization, Nginx reverse proxy, Cloudflare Tunnel setup, Cloudflare rate limiting, health endpoint, CI pipeline, and deployment automation with runbook documentation.

### Depends On

8C (backup scripts are included in the deployment). 8A and 8B must be complete so the deployed app has security and logging.

### Risk Level

**Medium.** This sub-phase changes how the app runs (containerized behind Nginx behind Cloudflare) which introduces environment-specific configuration. The Docker build from GitHub needs to be validated. Cloudflare Tunnel and Access configuration are new.

### Changes

#### Docker Finalization

1. **Audit and finalize the Dockerfile.** Ensure it:
   - Uses a specific Python base image version (not `latest`)
   - Installs only production dependencies
   - Runs as a non-root user
   - Sets `DEBUG=False` and appropriate production defaults
   - Exposes the correct port (e.g., 8000 for Gunicorn)
   - Includes a `HEALTHCHECK` instruction that hits the `/health` endpoint

2. **Audit and finalize `docker-compose.yml`.** Production compose file should include:
   - App service (Flask + Gunicorn)
   - PostgreSQL service with a named volume for data persistence
   - Nginx service (reverse proxy)
   - Environment variable configuration via `.env` file (not hardcoded secrets)
   - Restart policies (`restart: unless-stopped`)
   - Network isolation (app and Postgres on an internal network, Nginx on both internal and external)
   - Health checks for all services

3. **Create `docker-compose.dev.yml`.** Development override that:
   - Mounts source code as a volume for live reload
   - Runs Flask dev server instead of Gunicorn
   - Enables debug mode
   - Maps ports directly (no Nginx needed for dev)

4. **Validate the GitHub build.** Test that `docker build` succeeds when cloning from the GitHub repo (no local-only dependencies, no missing files in `.dockerignore`).

#### Nginx Configuration

5. **Create the Nginx config.** `nginx/nginx.conf`:
   - Reverse proxy to the Gunicorn app service
   - Serve static files directly (bypass Flask for `/static/`)
   - Security headers: `X-Content-Type-Options`, `X-Frame-Options`, `X-XSS-Protection`, `Strict-Transport-Security`, `Content-Security-Policy`
   - Gzip compression for text/html, text/css, application/javascript
   - Request size limits (prevent oversized uploads)
   - Connection timeouts

#### Gunicorn Configuration

6. **Create Gunicorn config.** `gunicorn.conf.py`:
   - Workers: 2-4 (suitable for single-user, adjust based on Proxmox resources)
   - Bind to `0.0.0.0:8000`
   - Access log format: JSON (consistent with structured logging)
   - Graceful timeout and worker timeout settings

#### Cloudflare Tunnel

7. **Set up Cloudflare Tunnel.** Document and script the Cloudflare Tunnel setup:
   - Install `cloudflared` on the Proxmox host (or as a container in docker-compose)
   - Create a tunnel and configure it to point to the Nginx service
   - DNS configuration for the chosen subdomain
   - Deliverable: documented steps in the runbook, plus a `cloudflared` config file

8. **Set up Cloudflare Access (zero-trust).** Add an Access policy in front of the tunnel:
   - Restrict access to allowed email addresses (your email, and later, family members)
   - This provides an additional authentication layer before requests even reach the app
   - Document the Cloudflare Access configuration in the runbook

9. **Configure Cloudflare rate limiting.** Add a Cloudflare WAF rule:
   - Rate limit the `/login` and `/auth/mfa-verify` paths at the Cloudflare level
   - This is the outer layer; the app-level rate limiting (8A) is the inner layer
   - Document the rule configuration

#### Health Endpoint

10. **Add a `/health` endpoint.** Returns 200 if the app and database are reachable, 500 otherwise.
    - `app/routes/health.py`: a simple Blueprint with one GET route
    - Checks: database connectivity (execute `SELECT 1`), app is initialized
    - Response: JSON `{"status": "healthy", "database": "connected"}` or `{"status": "unhealthy", "database": "error", "detail": "..."}`
    - No authentication required (so external monitors can hit it)
    - Excluded from audit logging and request logging (to avoid log noise)

#### CI Pipeline

11. **Create a basic CI pipeline.** Since you are new to CI, start simple with GitHub Actions:
    - `.github/workflows/ci.yml`:
      - **Trigger:** on push to `main` and on pull requests
      - **Steps:** checkout, set up Python, install dependencies, run `pylint`, run `pytest` with a PostgreSQL service container
    - This gives you automated test runs on every push without needing to remember to run them locally

12. **Create a deployment script.** `scripts/deploy.sh`: a shell script you run on the Proxmox host to deploy a new version:
    - Pull latest code from GitHub
    - Build the Docker image
    - Run database migrations (`flask db upgrade` inside the container)
    - Restart the app container (rolling restart if possible, otherwise stop/start)
    - Run a health check to verify the deploy succeeded
    - Roll back (restart the previous image) if the health check fails

#### Environment Configuration

13. **Create an `.env.example` file.** Documents all required environment variables:
    - `DATABASE_URL`
    - `SECRET_KEY`
    - `FLASK_ENV` (production/development)
    - `BACKUP_LOCAL_DIR`
    - `BACKUP_NAS_DIR`
    - `LOG_LEVEL`
    - `RATE_LIMIT_LOGIN` (attempts per window)
    - `RATE_LIMIT_WINDOW` (seconds)
    - `AUDIT_RETENTION_DAYS`

14. **Secret management.** Document how secrets (database password, Flask secret key, TOTP encryption key) are managed:
    - Stored in `.env` on the Proxmox host, not in the repo
    - `.env` is in `.gitignore`
    - `.env` is included in backups (or documented separately for disaster recovery)

### Test Gate

- [ ] `docker build` succeeds from a clean clone of the GitHub repo
- [ ] `docker-compose up` starts all services (app, Postgres, Nginx) and the app is reachable via Nginx
- [ ] `/health` returns 200 with database connected
- [ ] Static files served by Nginx (check response headers)
- [ ] Security headers present on all responses
- [ ] Cloudflare Tunnel routes traffic to the app
- [ ] Cloudflare Access blocks unauthenticated requests
- [ ] GitHub Actions CI runs tests on push to main
- [ ] `deploy.sh` successfully deploys a new version with zero manual steps after invocation
- [ ] `deploy.sh` rolls back on health check failure
- [ ] Application logs appear in JSON format in container stdout
- [ ] Promtail (or equivalent) scrapes logs and they appear in Grafana/Loki

---

## Sub-Phase 8E: Multi-User Groundwork

### Scope

Registration page, user_id query audit across all routes and services, and data isolation verification. No role system, no kid account features, no permission boundaries. Every user sees their own data only.

### Depends On

8A (authentication and MFA must be in place before adding new users). Can proceed in parallel with 8C and 8D.

### Risk Level

**Medium.** The user_id query audit touches every service and route. Missing a filter would leak data between users. This requires careful review.

### Changes

#### Registration Flow

1. **Create the registration page.** `app/templates/auth/register.html`:
   - Fields: email, display name, password, confirm password
   - Validation: email format, email uniqueness, password minimum length, password confirmation match
   - On success: create user, create default user_settings row, redirect to login
   - On failure: flash specific errors

2. **Create the registration route.** `app/routes/auth.py`: GET for the form, POST for submission.
   - Service layer handles user creation, bcrypt hashing, default settings
   - Log the registration event

3. **Decide on open vs. invite-only registration.** Two options (decide before building):
   - **Open:** Anyone with the URL can register. Simple but risky if exposed externally (mitigated by Cloudflare Access restricting who can reach the app)
   - **Invite-only:** Registration requires a valid invite token. You generate tokens from a CLI script or admin page. More secure but more work.
   - Recommendation: Open registration is fine since Cloudflare Access already restricts who can reach the app. Only people you add to the Cloudflare Access policy can even see the registration page.

4. **Seed script update.** Update `scripts/seed_user.py` to be idempotent (skip if the user already exists). The seeded user remains available for development and testing, but registration is the production path for new users.

#### user_id Query Audit

5. **Audit every database query in the application.** Systematically review every model query, service method, and route handler to confirm that it filters by `user_id` where applicable.
   - Deliverable: a checklist document organized by blueprint/service, listing every query and whether it filters by user_id
   - Scope: `budget.*`, `salary.*`, `auth.user_settings`, `auth.mfa_configs`
   - Excluded: `ref.*` tables (shared across all users), `system.audit_log` (already scoped by user_id column)

6. **Fix any queries that do not filter by user_id.** The schema already has user_id columns on all relevant tables, so this is about ensuring the application code actually uses them.
   - Common patterns to check:
     - Direct queries: `Account.query.filter_by(id=account_id)` should be `Account.query.filter_by(id=account_id, user_id=current_user.id)`
     - Relationship traversals: loading a transaction by ID and then accessing its account. If the transaction was loaded without a user_id filter, the account access is also unscoped.
     - Template context: Jinja2 templates that iterate over accounts, transactions, etc. The data source must be user-scoped.

7. **Add integration tests for data isolation.** Create a test suite that:
   - Creates two users with separate data (accounts, transactions, templates, salary profiles, etc.)
   - Logs in as user A and verifies that only user A's data is visible on every page and API endpoint
   - Logs in as user B and verifies the same
   - Attempts to access user A's resources by ID while logged in as user B (should return 403 or 404)
   - Covers: budget grid, accounts dashboard, transfers, salary, templates, mortgage, auto loan, investment, retirement, charts, settings

### Test Gate

- [ ] `pytest` passes (all existing tests plus new isolation tests)
- [ ] Registration creates a new user with default settings
- [ ] New user can log in and sees an empty budget (no data from the seeded user)
- [ ] Data isolation tests pass: user A cannot see user B's data on any endpoint
- [ ] Direct object access by ID returns 403/404 for unauthorized users
- [ ] user_id audit checklist is complete with all queries reviewed

---

## Additional Items (Not in a Sub-Phase)

These are items that surfaced during planning that are worth tracking but do not fit neatly into the sub-phases above. They can be addressed as small additions within the relevant sub-phase or deferred to a future phase.

### Database Maintenance

- **VACUUM/ANALYZE scheduling:** PostgreSQL auto-vacuum handles this by default, but the audit_log table may benefit from explicit scheduling if it grows large. Document the monitoring approach in the runbook.
- **Connection pooling:** Not needed for single-user. If multi-user load grows, add PgBouncer as a docker-compose service. Deferred.

### Data Integrity Checks

- **Maintenance script:** `scripts/integrity_check.py` that validates referential integrity, checks for orphaned records (transactions without templates, templates without categories), and flags balance anomalies (anchor balance jumps, missing pay periods in sequence).
- Recommendation: build this as part of 8C (backups) since it pairs well with backup verification. Run it as part of the verification script.

### Future Audit Log UI

- The audit log is backend-only for now (queried via psql). A future sub-phase could add a simple admin page with search by table, date range, and user. Keep this in mind but do not build it in Phase 8.

---

## Dependency Graph (Full)

```
                 UI/UX Remediation (complete)
                          |
                          v
               8A: Security Hardening
              (CSRF, password, MFA, rate
               limiting, sessions, errors)
                          |
                          v
             8B: Audit & Structured Logging
            (PG triggers, JSON logs, Loki)
                     /         \
                    v           v
    8C: Backups & DR       8E: Multi-User
   (pg_dump, NAS, restore,    Groundwork
    retention, verify)      (registration,
           |                 user_id audit,
           v                 isolation tests)
    8D: Production Deploy
   (Docker, Nginx, Cloudflare,
    health, CI, deploy script)
```

---

## Estimated Effort

| Sub-Phase                      | Estimated Weeks | Notes                                                                                       |
| ------------------------------ | --------------- | ------------------------------------------------------------------------------------------- |
| 8A: Security Hardening         | 2-3             | MFA is the largest item; CSRF audit could surface surprises                                 |
| 8B: Audit & Structured Logging | 1-2             | Triggers are mechanical; logging config is straightforward                                  |
| 8C: Backups & DR               | 1               | Scripting and documentation; NAS mount is the only environment-specific piece               |
| 8D: Production Deployment      | 2-3             | Docker finalization, Nginx, Cloudflare, CI are all new configuration; expect some debugging |
| 8E: Multi-User Groundwork      | 2-3             | user_id audit is tedious but critical; isolation tests are the safety net                   |
| **Total**                      | **8-12**        |                                                                                             |

---

## Risk Register

| #   | Risk                                                 | Likelihood | Impact   | Mitigation                                                                                                              |
| --- | ---------------------------------------------------- | ---------- | -------- | ----------------------------------------------------------------------------------------------------------------------- |
| R1  | MFA login flow breaks existing test authentication   | **High**   | Medium   | MFA is optional per user; test fixtures use users without MFA enabled. Add a separate test suite for the MFA flow.      |
| R2  | CSRF audit finds missing tokens on HTMX endpoints    | Medium     | **High** | Global HTMX header config (hx-headers on body) covers all HTMX requests at once. Manual forms are audited individually. |
| R3  | Audit triggers slow down recurrence regeneration     | Medium     | Medium   | Benchmark before and after. If needed, disable triggers during bulk operations within a transaction.                    |
| R4  | user_id filter missed on a query, leaking data       | Medium     | **High** | Systematic audit with checklist. Isolation integration tests as the safety net. Code review every query.                |
| R5  | Docker build fails from GitHub (missing local files) | Medium     | Low      | Test the build early in 8D. Maintain .dockerignore carefully.                                                           |
| R6  | Cloudflare Tunnel or Access misconfiguration         | Medium     | Medium   | Follow Cloudflare's official documentation. Test with a staging subdomain before pointing the production domain.        |
| R7  | NAS mount unreliable, backups silently fail          | Low        | **High** | Backup script checks NAS availability and exits non-zero on failure. Monitoring alert on backup failure (logged event). |
| R8  | Structured logging breaks existing log parsing       | Low        | Low      | Development environment keeps human-readable format. JSON is production only.                                           |
