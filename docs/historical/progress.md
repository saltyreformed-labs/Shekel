> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Shekel Budget App -- Progress Report

**Evaluated on:** 2026-03-23
**Evaluated by:** Claude Code (`/update-docs` skill)

---

## Summary

| Metric           | Count |
| ---------------- | ----- |
| Phases evaluated | 14    |
| Complete         | 13    |
| In progress      | 0     |
| Not started      | 0     |
| Deferred         | 1     |

---

## Recent Development Activity

```
2228c93 docs: add TOTP_ENCRYPTION_KEY generation instructions directly in README (P6-4)
d26b301 docs: add security hardening guidance for LAN and external deployments (P6-2)
baf6ef9 docs: add Backups section with urgency note and quick setup instructions (P6-3)
6c694bf docs: document REGISTRATION_ENABLED in README and .env.example (P6-1)
11e1540 fix(migrations): add missing CREATE TABLE for 7 salary schema tables
36f7464 test(health): add/verify error response sanitization regression tests (P5-6)
7f61748 test(auth): add/verify user deactivation session invalidation regression test (P5-4)
cc1f579 test(templates): add/verify IDOR regression test for preview_recurrence (P5-2)
536545c refactor(config): add explicit SQLALCHEMY_ENGINE_OPTIONS to ProdConfig (L4)
89a469d fix(transfers): add user_id ownership check to resolve_conflicts (H4-transfer)
447aeec fix(recurrence): add user_id ownership check to resolve_conflicts (H4)
2b03dfb refactor(tax): extract _load_tax_configs to app/services/tax_config_service.py (L1)
e06225a fix(tests): proper fixes for 3 CI failures -- no shortcuts
c2ac65b fix(tests): fix 3 CI test failures
eac98dd fix(ci): add --fail-on=E,F alongside --fail-under for two-layer pylint gate
f2c490e fix(ci): use --fail-under=9.0 instead of --fail-on=E,F for pylint
918d980 fix(settings): initialize mfa_enabled before conditional branch (E0601)
fa46476 fix(docker): add libc-dev to builder stage for psycopg2 compilation
d24a11a docs: rewrite README for Docker-based dev workflow and external volume setup
a40c05e Production readiness plans and prompts
```

Recent work since the last evaluation (2026-03-21) includes a **six-phase
production readiness audit** covering security fixes, architecture improvements,
test coverage hardening, and documentation:

- **Phase 1 -- Critical bug fixes:** is_active check on user_loader (session
  invalidation on deactivation), scenario_id filtering in carry-forward service,
  seed script production safety guard, pg_isready healthcheck fix.
- **Phase 2 -- High-severity security:** IDOR ownership checks on transaction
  form-rendering endpoints (quick_create, full_create, empty_cell),
  preview_recurrence start_period_id validation, rate limiting on /register,
  REGISTRATION_ENABLED toggle for disabling public registration.
- **Phase 3 -- Medium-severity fixes:** Health endpoint error sanitization
  (removed exception details from response), category delete user-scoped
  in-use check, forwarded_allow_ips restricted to RFC 1918, Python version
  pinned in Dockerfile, stale anchor balance warning.
- **Phase 4 -- Code quality and architecture:** Extracted _load_tax_configs to
  shared service (eliminated route-to-route import), added user_id ownership
  guards to resolve_conflicts in both recurrence_engine and transfer_recurrence,
  explicit SQLALCHEMY_ENGINE_OPTIONS on ProdConfig (pool_pre_ping, pool_recycle,
  connect_timeout).
- **Phase 5 -- Test coverage audit:** Verified all Phase 1-4 fixes have
  regression tests. Hardened 3 tests: nonexistent period fallback for
  preview_recurrence, user re-activation positive test, health endpoint
  logging preservation test. 1827 tests total, all passing.
- **Phase 6 -- Documentation:** Added Backups section (urgency note, quick
  setup, runbook link), Security section (LAN vs external deployment guidance),
  REGISTRATION_ENABLED in env table and troubleshooting, MFA Setup subsection
  with Fernet key generation instructions.

---

## Phase-by-Phase Assessment

### Phase 1 -- Replace the Spreadsheet

**Status:** Complete

**Expected deliverables:**

- Models: user, account, pay_period, transaction, category,
  recurrence_rule, transaction_template, ref tables
- Routes: auth, grid, transactions, templates, categories, pay_periods,
  accounts, settings
- Services: auth_service, balance_calculator, recurrence_engine,
  credit_workflow, carry_forward
- Templates: base layout, grid view, template management, category
  management, auth pages, settings, pay period generation
- Tests: balance calculator, recurrence engine, credit workflow,
  carry forward, auth routes, grid routes, transaction routes
- Scripts: seed_user, seed_ref_tables

**Found:**

- Models (all present): `user.py`, `account.py`, `pay_period.py`, `transaction.py`,
  `category.py`, `recurrence_rule.py`, `transaction_template.py`, `ref.py`
- Routes (all present): `auth.py`, `grid.py`, `transactions.py`, `templates.py`,
  `categories.py`, `pay_periods.py`, `accounts.py`, `settings.py`
- Services (all present): `auth_service.py`, `balance_calculator.py`,
  `recurrence_engine.py`, `credit_workflow.py`, `carry_forward_service.py`
- Templates (all present): `base.html`, `grid/` (11 files), `templates/` (2 files),
  `categories/` (2 files), `auth/` (6 files), `settings/` (10 files),
  `pay_periods/` (1 file), `accounts/` (4 files)
- Tests (all present): `test_balance_calculator.py`, `test_recurrence_engine.py`,
  `test_credit_workflow.py`, `test_carry_forward_service.py` (dedicated unit tests),
  `test_auth.py`, `test_grid.py`, `test_transactions.py` (via test_transaction_auth.py),
  `test_auth_required.py` (91 auth-required tests across all blueprints)
- Scripts (all present): `seed_user.py`, `seed_ref_tables.py`

**Notes:**

Additional deliverables beyond requirements: `transfer.py` model,
`transfer_template.py` model, `savings_goal.py` model (built ahead of v2 Phase 4),
`scenario.py` model stub (placeholder for deferred Phase 7). Carry forward service
named `carry_forward_service.py` (not `carry_forward.py`). Extra services include
`pay_period_service.py`, `account_resolver.py`, `transfer_recurrence.py`.
Recurrence rules now support optional `end_date` (migration `f8f8173ff361`).

---

### Phase 2 -- Paycheck Calculator

**Status:** Complete

**Expected deliverables:**

- Models: salary_profile, paycheck_deduction, tax_bracket, fica_config,
  state_tax_config, salary_raise
- Routes: salary
- Services: paycheck_calculator, tax_calculator
- Templates: salary breakdown, salary projection, salary form, salary list
- Tests: paycheck calculator, tax calculator
- Scripts: seed_tax_brackets

**Found:**

- Models (all present): `salary_profile.py`, `paycheck_deduction.py`,
  `tax_config.py` (contains tax brackets, FICA config, state tax config),
  `salary_raise.py`
- Routes (present): `salary.py`
- Services (all present): `paycheck_calculator.py`, `tax_calculator.py`
- Templates (all present): `salary/breakdown.html`, `salary/projection.html`,
  `salary/form.html`, `salary/list.html`, `salary/_deductions_section.html`,
  `salary/_raises_section.html`, `salary/tax_config.html`
- Tests (all present): `test_paycheck_calculator.py`, `test_tax_calculator.py`,
  `test_salary.py` (route tests)
- Scripts (present): `seed_tax_brackets.py`

**Notes:**

Tax models consolidated into single `tax_config.py` file rather than separate files
per model -- functionally equivalent. W-4 fields integration added beyond original
requirements. `target_account_id` column on `paycheck_deductions` is present (added
for Phase 5 integration). Tax rates updated to IRS/SSA/NCDOR 2026 actuals
(commit 4ad4a2e). State standard deduction support added (migration `02b1ff12b08c`).
Raise percentage inputs converted to human-readable format (e.g., 3.0% instead of
0.03). Effective year backfill migration added (`b4c5d6e7f8a9`).

---

### Savings & Accounts (v2 Phase 4, already built)

**Status:** Complete

**Expected deliverables:**

- Models: savings_goal, transfer (or transfer fields)
- Routes: accounts (with savings features), transfers
- Services: transfer_service, savings_goal_service (or similar)
- Templates: accounts dashboard (savings version)
- Tests: savings and transfer tests

**Found:**

- Models (all present): `savings_goal.py`, `transfer.py`, `transfer_template.py`
- Routes (all present): `accounts.py`, `transfers.py`, `savings.py`
- Services (all present): `savings_goal_service.py`, `transfer_recurrence.py`
- Templates (all present): `savings/dashboard.html`, `savings/goal_form.html`,
  `transfers/form.html`, `transfers/list.html`, `transfers/_transfer_cell.html`,
  `transfers/_transfer_empty_cell.html`, `transfers/_transfer_full_edit.html`,
  `transfers/_transfer_quick_edit.html`
- Tests (all present): `test_savings.py`, `test_transfers.py`,
  `test_savings_goal_service.py`, `test_transfer_recurrence.py`

**Notes:**

Savings has its own blueprint (`savings.py`) separate from accounts. Transfer
templates support recurrence rules for automated recurring transfers. Transfers
now also support optional end dates on recurrence rules.

---

### Phase 3 -- HYSA & Accounts Reorganization

**Status:** Complete

**Expected deliverables:**

- Models: hysa_params
- Services: interest_projection
- Templates: unified accounts dashboard (reorganized by category)
- Tests: test_interest_projection
- Schema change: category column on ref.account_types

**Found:**

- Models (present): `hysa_params.py`
- Services (present): `interest_projection.py`
- Templates (present): `accounts/hysa_detail.html`
- Tests (present): `test_interest_projection.py`, `test_balance_calculator_hysa.py`,
  `test_hysa.py` (15 route tests including negative paths, IDOR with DB
  verification, wrong-type guards, APY boundary validation)
- Schema change (present): `category` column on `ref.account_types` model
- Migration (present): `f1a2b3c4d5e6_add_hysa_and_account_categories.py`

**Notes:**

Accounts dashboard reorganized into category groupings in `accounts/list.html`. The
HYSA detail view (`hysa_detail.html`) provides interest projection display. Balance
calculator integrates HYSA interest projection for HYSA-type accounts (verified by
dedicated test file `test_balance_calculator_hysa.py`). Account types expanded from
2 to 18 entries with proper categories and display names (commit 244851c), updating
the seed script accordingly.

---

### Phase 4 -- Debt Accounts

**Status:** Complete

**Expected deliverables:**

- Models: mortgage_params, auto_loan_params, rate_history, escrow
- Routes: mortgage, auto_loan
- Services: amortization_engine, escrow_calculator
- Schemas: mortgage, auto_loan (Marshmallow)
- Templates: mortgage dashboard (with payoff, escrow, rate history
  fragments), auto loan dashboard
- Tests: amortization engine, escrow calculator, mortgage routes,
  auto loan routes

**Found:**

- Models (all present): `mortgage_params.py`, `auto_loan_params.py`
  (rate history and escrow components in mortgage_params.py)
- Routes (all present): `mortgage.py`, `auto_loan.py`
- Services (all present): `amortization_engine.py`, `escrow_calculator.py`
- Schemas: Validation handled in `schemas/validation.py` (consolidated approach)
- Templates (all present): `mortgage/dashboard.html`, `mortgage/_payoff_results.html`,
  `mortgage/_escrow_list.html`, `mortgage/_rate_history.html`, `mortgage/setup.html`,
  `auto_loan/dashboard.html`, `auto_loan/setup.html`
- Tests (all present): `test_amortization_engine.py`, `test_escrow_calculator.py`,
  `test_balance_calculator_debt.py`, `test_mortgage.py` (30 tests including
  negative paths, IDOR with DB verification, escrow/rate change edge cases),
  `test_auto_loan.py` (15 tests including negative paths)
- Migration (present): `a1b2c3d4e5f6_add_debt_account_tables.py`
- Chart integration: `chart_amortization.js`, `payoff_chart.js`

**Notes:**

Marshmallow schemas not split into per-type files (`mortgage.py`, `auto_loan.py`);
validation is consolidated in `schemas/validation.py`. This is a structural deviation
from the v3 addendum but functionally equivalent. Payoff calculator with Chart.js
inline chart was built as specified. Setup forms added for both mortgage and auto loan.
Escrow inflation rate and interest rate inputs converted to human-readable percentage
format.

---

### Phase 5 -- Investments & Retirement

**Status:** Complete

**Expected deliverables:**

- Models: investment_params, pension_profile
- Routes: investment, retirement
- Services: growth_engine, pension_calculator, retirement_gap_calculator
- Schemas: investment, pension (Marshmallow)
- Templates: investment dashboard, retirement dashboard, pension form,
  gap analysis fragment
- Tests: growth engine, pension calculator, retirement gap calculator,
  investment routes, retirement routes
- Schema change: target_account_id on salary.paycheck_deductions

**Found:**

- Models (all present): `investment_params.py`, `pension_profile.py`
- Routes (all present): `investment.py`, `retirement.py`
- Services (all present): `growth_engine.py`, `pension_calculator.py`,
  `retirement_gap_calculator.py`, `investment_projection.py` (additional)
- Schemas: Validation handled in `schemas/validation.py`
- Templates (all present): `investment/dashboard.html`, `investment/_growth_chart.html`,
  `retirement/dashboard.html`, `retirement/pension_form.html`,
  `retirement/_gap_analysis.html`
- Tests (all present): `test_growth_engine.py`, `test_pension_calculator.py`,
  `test_retirement_gap_calculator.py`, `test_investment_projection.py`,
  `test_investment.py` (21 tests including login-required, IDOR with DB
  verification, negative return rates), `test_retirement.py` (37 tests
  including pension CRUD negative paths, settings validation boundaries,
  login-required coverage)
- Schema change (present): `target_account_id` column on paycheck_deductions model
- Migration (present): `c3d4e5f6g7h8_add_investment_retirement_tables.py`
- Chart integration: `growth_chart.js`, `retirement_gap_chart.js`,
  `investment_form.js`

**Notes:**

Additional service `investment_projection.py` beyond requirements. Retirement
settings (SWR, tax rate, planned retirement date) integrated into settings dashboard
(`settings/_retirement.html`). Schemas consolidated rather than per-type files.
Pension calculator fixed to properly account for recurring raises when computing
highest-paid years (commit 1fcde46). All percentage inputs (return rates, employer
contribution rates, SWR) converted to human-readable format.

---

### Phase 6 -- Visualization

**Status:** Complete

**Expected deliverables:**

- Chart.js integration
- Balance-over-time line chart
- Category spending breakdown (bar chart)
- Budget vs. actuals comparison chart
- Net pay trajectory chart
- Scenario comparison overlay chart (may depend on Phase 7)

**Found:**

- Chart.js integration (present): Referenced in `base.html`
- Charts dashboard: `charts/dashboard.html` with 7 chart fragment templates
- Chart fragments: `_balance_over_time.html`, `_spending_category.html`,
  `_budget_vs_actuals.html`, `_net_pay.html`, `_net_worth.html`,
  `_amortization.html`, `_error.html`
- Chart JS modules (16 files): `chart_balance.js`, `chart_spending.js`,
  `chart_budget.js`, `chart_net_pay.js`, `chart_net_worth.js`,
  `chart_amortization.js`, `chart_theme.js`, `chart_slider.js`,
  `growth_chart.js`, `payoff_chart.js`, `retirement_gap_chart.js`,
  `investment_form.js`, plus others
- Routes: `charts.py`
- Services: `chart_data_service.py`
- Tests: `test_charts.py`, `test_chart_data_service.py`

**Notes:**

Exceeds requirements -- includes net worth projection chart, amortization chart,
Chart.js theming system (`chart_theme.js`), and interactive sliders for SWR/return
rate adjustments (`chart_slider.js`). Scenario comparison overlay deferred (depends
on Phase 7). Error handling partial (`_error.html`) for chart load failures.
Balance-over-time chart rendering issue fixed (commit ffa4614). Float() conversions
isolated at Chart.js presentation boundary (commit 03acb69).

---

### Phase 7 -- Scenarios

**Status:** Deferred (per v3 addendum, moved from v2 Phase 3)

**Expected deliverables (when built):**

- Models: scenario (or scenario fields on existing models)
- Routes: scenarios
- Services: scenario_service (clone, diff)
- Templates: scenario comparison view

**Found:**

- Models: `scenario.py` exists with `is_baseline` flag -- stub only
- Routes: No scenarios route
- Services: No scenario_service
- Templates: No scenario comparison view
- UI spec: `docs/scenario_ui_requirements.md` exists

**Notes:**

The model file is a placeholder established in Phase 1. No functional scenario
logic (clone, compare, diff) has been implemented. The UI specification has been
drafted. Settings templates include scenario selector slots (future-proofing from
UI/UX remediation Phase 5).

---

### Phase 8A -- Security Hardening

**Status:** Complete

**Expected deliverables:**

- Password change: route + auth_service.change_password()
- CSRF audit: all POST forms have csrf_token, HTMX header injection
- Session management: session_invalidated_at column, load_user check
- Rate limiting: Flask-Limiter on auth routes
- MFA/TOTP: mfa_service.py, mfa routes, mfa templates, pyotp +
  qrcode + cryptography in requirements.txt
- Custom error pages: 404, 500, 429, 403
- Tests: test_mfa_service, test_errors, test_auth (password change,
  session management, MFA setup/login/disable)

**Found:**

- Password change (present): Route in `auth.py`, service in `auth_service.py`
- CSRF (present): All 70+ forms covered, HTMX header injection in `base.html`,
  CSRF hidden inputs added to all HTMX forms with POST fallback (commit de9e456)
- Session management (present): `session_invalidated_at` column (migration
  `2ae345ea9048`), logout-all-sessions functionality
- Rate limiting (present): Flask-Limiter on login and MFA verify (5/15min)
- MFA/TOTP (all present): `mfa_service.py`, routes in `auth.py`, templates
  (`mfa_setup.html`, `mfa_verify.html`, `mfa_backup_codes.html`, `mfa_disable.html`),
  settings partial (`_mfa_setup.html`, `_security.html`)
- Custom error pages (all present): `errors/400.html`, `errors/403.html`,
  `errors/404.html`, `errors/429.html`, `errors/500.html`
- Tests (all present): `test_mfa_service.py`, `test_errors.py`, `test_auth.py`
- Scripts: `reset_mfa.py` (MFA recovery)
- Additional: Security headers (CSP, X-Frame-Options, Referrer-Policy),
  encrypted MFA secrets (Fernet), open redirect prevention (commit fa2e44c)

**Notes:**

All 5 custom error pages now present (400 and 403 added in commit 89c83ed,
completing the set from the previous evaluation). Security headers exceed Phase 8A
requirements -- CSP implemented with external JS migration (commit 47c1d56). Inline
scripts moved to external files for CSP compliance.

---

### Phase 8B -- Audit & Structured Logging

**Status:** Complete

**Depends on:** 8A (complete)

**Expected deliverables:**

- system.audit_log table (migration)
- Audit trigger function on budget.transactions (and other tables)
- scripts/audit_cleanup.py
- Structured JSON logging (production config)
- Request ID correlation in logs
- Auth event logging

**Found:**

- Audit log table (present): Migration `a8b1c2d3e4f5_add_audit_log_and_triggers.py`
- Triggers (present): Generic PL/pgSQL function on 22 financial/auth tables
- Cleanup script (present): `scripts/audit_cleanup.py` (--days, --dry-run flags)
- Structured logging (present): `app/utils/logging_config.py`, `python-json-logger`
- Request ID (present): UUID4 generation, `X-Request-Id` response header
- Request duration tracking (present): Configurable slow threshold (WARNING >=500ms)
- Auth event logging (present): `app/utils/log_events.py`, 9 auth routes instrumented
- Business event logging (present): Recurrence engine, carry forward service
- Performance benchmarks: `tests/test_performance/test_trigger_overhead.py`
- Monitoring: `monitoring/promtail-config.yml`, `monitoring/README.md`
- Tests: `test_audit_triggers.py` (23 tests), `test_log_events.py` (9 tests),
  `test_logging_config.py` (9 tests), `test_audit_cleanup.py` (6 tests)

**Notes:**

Flask middleware sets `app.current_user_id` per request for trigger capture.
Trigger overhead benchmarked at <5% on recurrence engine (well under 20% threshold).
Gunicorn access log disabled in favor of Flask JSON logging.

---

### Phase 8C -- Backups & Disaster Recovery

**Status:** Complete

**Depends on:** 8B (complete)

**Expected deliverables:**

- scripts/backup.sh
- scripts/backup_retention.sh (tiered retention)
- scripts/restore.sh
- scripts/verify_backup.sh
- Cron configuration documentation
- NAS mount documentation
- Restore procedure in runbook

**Found:**

- Backup script (present): `scripts/backup.sh` (pg_dump, gzip, local + NAS copy,
  optional GPG encryption)
- Retention script (present): `scripts/backup_retention.sh` (7-day daily,
  4-week weekly, 6-month monthly tiers)
- Restore script (present): `scripts/restore.sh` (interactive confirm,
  drop/recreate, atomic restore, encrypted backup support, post-restore verify)
- Verify script (present): `scripts/verify_backup.sh` (temp DB restore,
  7 sanity checks, integrity check suite)
- Integrity check (present): `scripts/integrity_check.py` (33 checks,
  4 categories, CLI + importable)
- Documentation (present): `docs/backup_runbook.md` (cron, NAS mount,
  encryption, restore procedure, troubleshooting)
- Tests: `test_integrity_check.py` (24 tests)

**Notes:**

Exceeds requirements -- includes optional GPG encryption for backups, integrity
check script (33 checks across referential, orphan, balance, and consistency
categories), and encrypted backup auto-detection in restore script.

---

### Phase 8D -- Production Deployment

**Status:** Complete

**Depends on:** 8A, 8B, 8C (all complete)

**Expected deliverables:**

- Dockerfile (finalized for production)
- docker-compose.yml (production config with Nginx)
- Nginx reverse proxy configuration
- Cloudflare Tunnel setup or documentation
- Cloudflare rate limiting configuration
- Health check endpoint (/health or similar)
- CI pipeline (.github/workflows/ or similar)
- scripts/deploy.sh
- Deployment runbook

**Found:**

- Dockerfile (present): Multi-stage build, Python 3.14-slim, non-root user
- docker-compose.yml (present): app + PostgreSQL + Nginx services with health checks
- docker-compose.dev.yml (present): Development config with separate test DB
- Nginx config (present): `nginx/nginx.conf` (reverse proxy, static files,
  security headers, gzip, CF-Connecting-IP real IP propagation)
- Cloudflare Tunnel (present): `cloudflared/config.yml`, documented in runbook
- CI pipeline (present): `.github/workflows/ci.yml` (lint + test on push/PR),
  `.github/workflows/docker-publish.yml` (Docker image publishing)
- Health endpoint (present): `app/routes/health.py` (DB connectivity check,
  no auth required, excluded from logging)
- Deploy script (present): `scripts/deploy.sh` (pull, build, migrate, restart,
  health verify, rollback on failure)
- Gunicorn config (present): `gunicorn.conf.py`
- Entrypoint (present): `entrypoint.sh` (idempotent DB init, migrate, seed)
- Runbook (present): `docs/runbook.md` (unified ops runbook covering deployment,
  backup/restore, security ops, monitoring, Cloudflare management)
- Additional docs: `docs/runbook_secrets.md`
- Tests: `test_health.py` (6 tests)

**Notes:**

Phase 8D was implemented in three sub-phases (8D-1 through 8D-3), each with its
own implementation plan in `docs/`. The Cloudflare Access zero-trust policy and WAF
rate limiting are documented in the runbook and implementation plans. Nginx config
includes `set_real_ip_from` directives for Cloudflare IP ranges and
`real_ip_header CF-Connecting-IP` for correct client IP propagation. All Python
dependencies upgraded for production readiness (commit 693d4b5).

A new-user setup audit (`docs/new_user_setup_audit.md`) identified several issues
with the end-user Docker setup experience: docker-compose.yml uses `build: .`
instead of the GHCR image, entrypoint runs seed_tax_brackets before seed_user, and
the README lacks Docker Quick Start documentation. An implementation plan
(`docs/new_user_setup_implementation_plan.md`) has been drafted to address these.

---

### Phase 8E -- Multi-User Groundwork

**Status:** Complete

**Depends on:** 8A (complete). Proceeded in parallel with 8C and 8D.

**Expected deliverables:**

- Registration route and template
- user_id filtering audit across all queries
- Data isolation integration tests (two users, verify separation)
- Direct object access returns 403/404 for unauthorized users

**Found:**

- Registration (present): `app/templates/auth/register.html`, register routes
  in `auth.py` (GET `register_form`, POST `register`)
- Data isolation tests (present): `tests/test_integration/test_data_isolation.py`
  (456 lines, 26 test functions)
- Access control tests (present): `tests/test_integration/test_access_control.py`
  (1083 lines, 66 test functions)
- Auth-required tests (present): `tests/test_routes/test_auth_required.py`
  (91 test functions covering all 19 blueprints)
- Transaction auth tests (present): `tests/test_routes/test_transaction_auth.py`
- Adversarial tests (present): `tests/test_adversarial/test_hostile_qa.py`
  (26 tests for XSS, SQL injection, path traversal, CSRF)
- Implementation plan: `docs/phase_8e_implementation_plan.md`

**Notes:**

Open registration approach (Cloudflare Access restricts who can reach the app).
Comprehensive data isolation testing with 92 combined test functions across
isolation and access control test files. User_id filtering audit completed
as part of the implementation.

---

## UI/UX Remediation

**Status:** Complete (5 phases)

- Phase 1: Visual consistency (icons, active nav state, breadcrumbs, headings)
- Phase 2: Nomenclature ("Templates" to "Recurring Transactions", formatting)
- Phase 3: Settings consolidation (Categories, Pay Periods, Tax Config into Settings)
- Phase 4: Navigation restructure (dropdown groups, merged "Accounts & Savings")
- Phase 5: Future-proofing (scenario selector slots for Phase 7)

---

### Production Readiness Audit

**Status:** Complete (6 sub-phases, 2026-03-21 through 2026-03-23)

**Scope:** Cross-cutting hardening effort addressing 23 findings from a
comprehensive security and code quality audit (`docs/production_readiness_audit_v2.md`).

**Found (all implemented):**

- P1 (critical bugs): `is_active` check in `user_loader`, `scenario_id` parameter
  added to carry-forward service, seed script `FLASK_ENV != production` guard
- P2 (high-severity security): Ownership checks on `get_quick_create`,
  `get_full_create`, `get_empty_cell`, `preview_recurrence`; rate limit on
  `/register`; `REGISTRATION_ENABLED` config toggle
- P3 (medium-severity): Health endpoint `"detail"` key removed, category delete
  `in_use` query scoped by `user_id` via PayPeriod join, `forwarded_allow_ips`
  restricted to RFC 1918, Python pinned to 3.14.3, stale anchor warning added
- P4 (architecture): `_load_tax_configs` extracted to `app/services/tax_config_service.py`
  (eliminated route-to-route import and duplicate), `user_id` ownership guard on
  `resolve_conflicts` in both `recurrence_engine.py` and `transfer_recurrence.py`,
  explicit `SQLALCHEMY_ENGINE_OPTIONS` on `ProdConfig`
- P5 (test coverage): All P1-P4 fixes verified to have regression tests; 3 tests
  hardened (preview nonexistent period, user reactivation, health logging)
- P6 (documentation): README Backups section, Security section (LAN/external),
  MFA Setup subsection, REGISTRATION_ENABLED in env table and troubleshooting

**Notes:**

Test count increased from 1772 to 1827 (+55 tests). Pylint score maintained at
9.35/10. New service module `tax_config_service.py` created. No new route modules
or model files -- all changes were to existing code and tests.

---

## Requirements Drift

- **Schema consolidation:** The v3 addendum specifies separate Marshmallow schema
  files per account type (`schemas/mortgage.py`, `schemas/auto_loan.py`,
  `schemas/investment.py`, `schemas/pension.py`). The implementation uses a single
  `schemas/validation.py` file. Functionally equivalent.

- **Phase numbering:** The project evolved its own phase numbering that blends v2
  and v3 schemes. HYSA, debt accounts, and original savings are grouped under
  "Phase 4: Accounts & Transfers" in the existing progress tracker, while v3
  assigns them to separate phases (3, 4). This report uses v3 numbering for clarity.

- **Smart Features / Notifications (v3 Phases 9-10):** Referenced in the v3 addendum
  but not tracked in current project planning. These remain future scope.

- **Scenario model stub:** `app/models/scenario.py` exists from Phase 1 as a
  placeholder. No functional scenario logic has been built.

- **Percentage input format:** All percentage inputs have been converted from raw
  decimal format (e.g., 0.03) to human-readable format (e.g., 3.0%). This is a
  UX improvement not specified in the original requirements. The conversion happens
  at the route layer (dividing by 100 before storing, multiplying by 100 before
  displaying).

- **Account type expansion:** `ref.account_types` has been expanded from the
  original 2 types (checking, savings) to 18 types covering all supported account
  categories (asset, liability, retirement, investment). The seed script was updated
  accordingly.

- **Docker end-user setup:** Resolved. docker-compose.yml now uses the GHCR
  image, README has full Docker Quick Start documentation, and entrypoint seed
  ordering has been fixed. See the README Quick Start (Docker) section.

- **Production readiness audit:** A six-phase hardening effort (separate from the
  feature phases) was completed between 2026-03-21 and 2026-03-23. This addressed
  23 findings from a comprehensive security and code quality audit. The fixes span
  Phases 1, 2, 3, 8A, and 8E deliverables but are tracked as a single audit effort.

---

## Suggested Next Steps

1. **Phase 7 -- Scenarios:** The only remaining deferred feature phase. The
   `scenario.py` model stub and UI spec (`docs/scenario_ui_requirements.md`)
   are in place. Depends on all current phases being stable, which they are.
   This adds clone, compare, and diff functionality for what-if budget analysis.

2. **v3 Phase 9 -- Smart Features:** Rolling averages for expense tracking,
   inflation adjustment on long-term projections. The inflation field on paycheck
   deductions already exists but global projection adjustment is not implemented.

3. **v3 Phase 10 -- Notifications:** In-app alerts and email notifications
   including loan payoff milestones, retirement goal milestones, and contribution
   limit warnings. Requires the least architectural change.

---

## Production Readiness Audit

**Status:** Complete (6 sub-phases, 2026-03-21 through 2026-03-23)

A dedicated hardening effort that cross-cut existing phases to address findings
from a comprehensive security and code quality audit. All fixes include
regression tests. Pylint score maintained at 9.35/10.

| Sub-Phase | Focus | Key Fixes |
| --------- | ----- | --------- |
| P1 | Critical bugs | is_active session check, carry-forward scenario_id filter, seed production guard |
| P2 | High-severity security | IDOR on 3 transaction form endpoints + preview_recurrence, rate limit /register, REGISTRATION_ENABLED toggle |
| P3 | Medium-severity | Health endpoint info leak, category delete cross-user scope, forwarded_allow_ips, Python pin, stale anchor warning |
| P4 | Architecture | Extract tax config service (route-to-route import), resolve_conflicts ownership guards, ProdConfig pool settings |
| P5 | Test coverage | Audited all P1-P4 fixes for regression tests, hardened 3 weak tests (1827 total, all passing) |
| P6 | Documentation | README Backups/Security/MFA sections, REGISTRATION_ENABLED docs, env table updates |

---

## Test Suite Health

**Total:** 1827 test functions across 65 test files
**Runtime:** ~7 minutes (full suite)

| Category                | Files | Approx. Tests |
| ----------------------- | ----- | ------------- |
| Route tests             | 24    | ~880          |
| Service tests           | 23    | ~560          |
| Integration tests       | 6     | ~125          |
| Schema/model tests      | 2     | ~80           |
| Script tests            | 3     | ~40           |
| Utility tests           | 3     | ~30           |
| Adversarial tests       | 1     | ~26           |
| Performance benchmarks  | 1     | ~3            |
| Config tests            | 2     | ~20           |

Test remediation work units (WU0 through WU5.4) systematically added:
- Auth-required coverage for all 19 blueprints (91 tests)
- IDOR tests with DB state verification (expire_all + re-query pattern)
- Schema validation boundary tests (negative values, out-of-range, non-numeric)
- Account type guard tests (wrong type redirects)
- Nonexistent resource handling (404/302 with flash messages)
- Financial account negative paths across all 5 account types
- Dedicated carry_forward_service unit tests (commit 752eef7)
- Production readiness regression tests: ownership guards, deactivation flow,
  health sanitization, preview fallback, tax config service (Phase 5 audit)
