> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Phase 8C: Backups & Disaster Recovery -- Implementation Plan

## Overview

This plan implements Sub-Phase 8C from the Phase 8 Hardening & Ops Plan. It covers automated PostgreSQL backups with tiered retention, NAS copy, a restore script, backup verification, and a data integrity check script. It also implements the `scripts/integrity_check.py` recommended in the master plan's "Additional Items" section.

**Pre-existing infrastructure discovered during planning:**

- No backup, restore, or retention scripts exist anywhere in the project. All scripts in `scripts/` are Python-based application utilities (seed, init, audit cleanup, MFA reset, benchmark). Phase 8C is entirely additive.
- The Dockerfile (`Dockerfile:24`) already installs `postgresql-client` in the runtime stage, which provides `pg_dump`, `psql`, `pg_isready`, and `pg_restore` inside the app container. No Docker changes needed.
- Docker Compose (`docker-compose.yml:11-26`) defines the PostgreSQL service as `db` (container name `shekel-db`) with `postgres:16-alpine`, credentials `shekel_user` / `${POSTGRES_PASSWORD}`, database `shekel`, and a named volume `pgdata`.
- The `DATABASE_URL` is constructed in `docker-compose.yml:38` as `postgresql://shekel_user:${POSTGRES_PASSWORD}@db:5432/shekel`. Individual components are also passed as `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` (`docker-compose.yml:39-42`).
- The entrypoint (`entrypoint.sh:8-15`) already uses `pg_isready` and `psql` with these `DB_*` environment variables, establishing the pattern for how shell scripts should access the database.
- Five PostgreSQL schemas exist: `ref`, `auth`, `budget`, `salary`, `system` (`scripts/init_db.sql:5-9`). The backup must capture all five schemas plus the `public.alembic_version` table.
- The `system.audit_log` table (created in 8B) uses auto-increment `BIGSERIAL` IDs and `TIMESTAMPTZ` columns. It is a key table for backup verification.
- Alembic is configured via `alembic.ini` with `script_location = migrations`. The `scripts/init_database.py` script shows how to invoke Alembic programmatically: `command.upgrade(alembic_cfg, "head")` (`scripts/init_database.py:50-54`). The restore script will use `flask db upgrade` which invokes this same path.
- Existing Python scripts follow a consistent pattern: `sys.path` manipulation, `create_app()` + `app.app_context()` for database access, `argparse` for CLI arguments, Google-style docstrings, core logic separated from CLI wrapper for testability (`scripts/audit_cleanup.py` is the canonical example).
- The `monitoring/README.md:127-130` documents the existing cron pattern for running scripts inside the app container: `docker exec shekel-app python scripts/audit_cleanup.py`. Phase 8C backup scripts will follow the same `docker exec` pattern.
- No NAS mount configuration exists in the project. This is expected -- NAS mounts are host-level and documented in the runbook rather than managed by Docker.
- The test database setup in `tests/conftest.py` uses session-scoped app/db fixtures with table truncation between tests. The `_create_audit_infrastructure()` helper creates the `system.audit_log` table and all audit triggers. Tests for `integrity_check.py` will follow the same pattern as `tests/test_scripts/test_audit_cleanup.py`.

**New dependencies required:** None (shell scripts use standard Unix tools; `integrity_check.py` uses only the existing SQLAlchemy stack).

**Alembic migration required:** None (no schema changes).

---

## Pre-Existing Infrastructure

### Docker Configuration

| Component | Status | Location | Impact on 8C |
|-----------|--------|----------|--------------|
| PostgreSQL service | Running as `db` / `shekel-db` | `docker-compose.yml:12-26` | Backup scripts target this service |
| `postgresql-client` in app image | Installed | `Dockerfile:24` | `pg_dump` and `psql` are available inside `shekel-app` container |
| `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` env vars | Passed to app | `docker-compose.yml:39-42` | Shell scripts inside the container can use these directly |
| `DATABASE_URL` env var | Constructed from components | `docker-compose.yml:38` | Python scripts (integrity check) use this via Flask config |
| Named volume `pgdata` | Database persistence | `docker-compose.yml:56` | Not directly relevant; backup uses `pg_dump`, not volume copy |
| Named volume `applogs` | Log persistence | `docker-compose.yml:57` | Not backed up (logs are ephemeral; Loki handles retention) |

### Database Configuration

| Component | Status | Location | Impact on 8C |
|-----------|--------|----------|--------------|
| Five schemas: `ref`, `auth`, `budget`, `salary`, `system` | Created by init_db.sql | `scripts/init_db.sql:5-9` | `pg_dump` must use `--schema` flags or dump all schemas |
| `public.alembic_version` table | Tracks migration state | `scripts/init_db.sql:13-16` | Must be included in backup; used by `flask db upgrade` on restore |
| `system.audit_log` table | 8B audit trail | `tests/conftest.py:267-291` (structure) | Key table for backup verification sanity checks |
| Audit triggers on 21 tables | Active | `tests/conftest.py:363-396` (table list) | Triggers are recreated by Alembic migration; not dumped by `pg_dump --data-only` |

### Existing Scripts Convention

| Pattern | Example | Location | Impact on 8C |
|---------|---------|----------|--------------|
| `sys.path.insert(0, ...)` for imports | All Python scripts | `scripts/audit_cleanup.py:30` | `integrity_check.py` will follow same pattern |
| `create_app()` + `app.app_context()` | CLI wrapper | `scripts/audit_cleanup.py:124-129` | `integrity_check.py` CLI mode uses same pattern |
| Core logic separated from CLI | `execute_cleanup()` vs `run_cleanup()` | `scripts/audit_cleanup.py:59-129` | `integrity_check.py` exposes testable core functions |
| `argparse` for CLI arguments | `parse_args()` | `scripts/audit_cleanup.py:33-56` | `integrity_check.py` uses `argparse` with `--database-url` override |
| `docker exec` for cron invocation | Documented cron entry | `monitoring/README.md:129` | Backup/restore/verify scripts follow `docker exec` or host-level pattern |

### Entrypoint and Migration Pattern

| Component | Location | Impact on 8C |
|-----------|----------|--------------|
| `pg_isready` wait loop | `entrypoint.sh:8-10` | Restore script uses same wait pattern after database recreation |
| Schema creation via `psql -f init_db.sql` | `entrypoint.sh:15` | Restore script recreates schemas before `pg_restore` |
| `python scripts/init_database.py` (runs Alembic) | `entrypoint.sh:20` | Restore script runs `flask db upgrade` for pending migrations |
| Alembic config: `script_location = migrations` | `alembic.ini:9` | Programmatic Alembic invocation uses same config |

### Test Infrastructure

| Component | Location | Impact on 8C |
|-----------|----------|--------------|
| Session-scoped app/db fixtures | `tests/conftest.py:44-88` | `integrity_check.py` tests use same fixtures |
| Table truncation between tests | `tests/conftest.py:97-133` | Tests start with clean database state |
| Audit infrastructure in tests | `tests/conftest.py:259-396` | `system.audit_log` available for integrity check tests |
| Script test pattern | `tests/test_scripts/test_audit_cleanup.py` | Template for `test_integrity_check.py` structure |
| Reference data seeded once | `tests/conftest.py:399-443` | `ref.*` tables are populated; integrity checks can verify them |

---

## Script Execution Context Recommendation

**Decision: Shell scripts run on the Arch Linux host via `docker exec` into the app container. The integrity check Python script runs inside the container.**

### Options Analyzed

| Option | Pros | Cons |
|--------|------|------|
| **A: Scripts on host, `docker exec` into containers** | Cron runs natively on host; NAS mount is host-level; scripts can access both local and NAS paths directly | `pg_dump`/`psql` must run via `docker exec shekel-db`; two-hop execution |
| **B: Scripts inside app container** | `pg_dump`/`psql` already installed; `DB_*` env vars available; single execution context | No access to NAS mount; cron must run inside container or be triggered externally; container restart loses cron |
| **C: Scripts on host with `pg_dump` installed natively** | Direct database access; direct NAS access; simplest cron | Requires installing `postgresql-client` on host; connection goes through Docker network mapping |

**Recommendation: Hybrid approach (Option A+B).**

The shell scripts (`backup.sh`, `backup_retention.sh`, `restore.sh`, `verify_backup.sh`) run **on the Arch Linux host** because:

1. **NAS access:** The NAS mount point (`/mnt/nas/backups/shekel/`) exists on the host filesystem, not inside containers.
2. **Cron scheduling:** Host-level cron (`systemd` timers or crontab) is reliable and persists across container restarts.
3. **Local backup directory:** `/var/backups/shekel/` is a host path.
4. **Container orchestration:** The restore script must stop and restart the app container, which requires host-level Docker access.

Database commands (`pg_dump`, `psql`) are executed via `docker exec shekel-db ...` from within the host scripts. This avoids installing PostgreSQL client tools on the host and ensures version compatibility with the containerized PostgreSQL 16.

The integrity check script (`integrity_check.py`) runs **inside the app container** via `docker exec shekel-app python scripts/integrity_check.py` because it uses SQLAlchemy and the Flask application context, exactly like `audit_cleanup.py`.

---

## NAS Protocol Recommendation

**Decision: Document both NFS and CIFS/SMB. The user selects based on their NAS hardware.**

### NFS

**Pros:** Native Linux support, low overhead, no credential management for trusted networks, better performance for large sequential writes (backup files).

**Cons:** Security relies on IP-based trust (no encryption by default); NFSv4 can add Kerberos but adds complexity.

**Best for:** Synology, TrueNAS, or any NAS on a trusted LAN.

**fstab entry:**
```
nas-ip:/volume1/backups/shekel  /mnt/nas/backups/shekel  nfs  defaults,soft,timeo=150,retrans=3  0  0
```

### CIFS/SMB

**Pros:** Native support on Windows-based NAS devices, credential-based authentication, encrypted transport (SMB3).

**Cons:** Slightly higher overhead than NFS; requires `cifs-utils` package; credential file management.

**Best for:** Windows Server, some QNAP devices, or environments requiring encrypted transport.

**fstab entry:**
```
//nas-ip/backups/shekel  /mnt/nas/backups/shekel  cifs  credentials=/root/.nas-credentials,uid=root,gid=root,file_mode=0600,dir_mode=0700  0  0
```

Both options are documented in the runbook with verification steps.

---

## Backup Encryption Recommendation

**Decision: Add optional GPG encryption to the backup pipeline.**

### Rationale

The backup files contain the entire database, including:
- User credentials (bcrypt hashes, encrypted TOTP secrets)
- Financial transaction data (amounts, categories, projections)
- Salary and tax information
- Session tokens and MFA backup code hashes

Unencrypted backups on a NAS are a data exposure risk if the NAS is compromised or a backup file is accidentally shared.

### Approach

- **GPG symmetric encryption** (`gpg --symmetric --cipher-algo AES256`) with a passphrase stored in an environment variable (`BACKUP_ENCRYPTION_PASSPHRASE`).
- Encryption is **optional**: if `BACKUP_ENCRYPTION_PASSPHRASE` is not set, backups are stored unencrypted (plain `.sql.gz`). If set, the output is `.sql.gz.gpg`.
- The verify and restore scripts detect the `.gpg` extension and decrypt automatically.
- Passphrase is documented in the runbook as a critical secret that must be stored separately from the backups (not on the NAS).

### Tradeoff

| Benefit | Cost |
|---------|------|
| Financial data protected at rest | Passphrase must be managed as a secret |
| NAS compromise does not expose data | Restore requires passphrase; lost passphrase = lost backups |
| Meets data-at-rest security best practices | ~5-10% slower backup due to encryption overhead |
| `gpg` is pre-installed on Arch Linux | Adds complexity to backup/restore/verify scripts |

The cost is minimal and the benefit is significant for financial data. Encryption is implemented but opt-in.

---

## Integrity Check Scope

Based on analysis of all 35 tables across 5 schemas, the following integrity checks are organized by category.

### Category 1: Referential Integrity (FK Validation)

These checks verify that foreign key references point to existing rows. While PostgreSQL enforces FK constraints on write, data corruption, partial restores, or manual SQL operations could introduce violations.

| Check ID | Table | FK Column | References | Description |
|----------|-------|-----------|------------|-------------|
| FK-01 | `budget.accounts` | `user_id` | `auth.users.id` | Accounts without a valid user |
| FK-02 | `budget.accounts` | `account_type_id` | `ref.account_types.id` | Accounts with invalid account type |
| FK-03 | `budget.accounts` | `current_anchor_period_id` | `budget.pay_periods.id` | Accounts pointing to nonexistent anchor period |
| FK-04 | `budget.transactions` | `template_id` | `budget.transaction_templates.id` | Transactions referencing deleted templates (SET NULL should handle this, but verify nulls vs. dangling IDs) |
| FK-05 | `budget.transactions` | `pay_period_id` | `budget.pay_periods.id` | Transactions in nonexistent pay periods |
| FK-06 | `budget.transactions` | `scenario_id` | `budget.scenarios.id` | Transactions in nonexistent scenarios |
| FK-07 | `budget.transactions` | `category_id` | `budget.categories.id` | Transactions with invalid category |
| FK-08 | `budget.transfers` | `from_account_id` | `budget.accounts.id` | Transfers from nonexistent accounts |
| FK-09 | `budget.transfers` | `to_account_id` | `budget.accounts.id` | Transfers to nonexistent accounts |
| FK-10 | `budget.transaction_templates` | `category_id` | `budget.categories.id` | Templates with invalid category |
| FK-11 | `budget.transaction_templates` | `account_id` | `budget.accounts.id` | Templates for nonexistent accounts |
| FK-12 | `salary.salary_profiles` | `scenario_id` | `budget.scenarios.id` | Salary profiles in nonexistent scenarios |
| FK-13 | `salary.salary_profiles` | `template_id` | `budget.transaction_templates.id` | Salary profiles linked to deleted templates |

### Category 2: Orphan Detection

Records that exist but are functionally disconnected from the data model.

| Check ID | Description | SQL Logic |
|----------|-------------|-----------|
| OR-01 | Transaction templates with no active recurrence rule and no transactions | Templates where `recurrence_rule_id IS NULL` and no rows in `budget.transactions` reference the template |
| OR-02 | Recurrence rules not referenced by any template | Rules where no `transaction_templates.recurrence_rule_id` or `transfer_templates.recurrence_rule_id` points to them |
| OR-03 | Categories not used by any template or transaction | Categories where no `transaction_templates.category_id` or `transactions.category_id` references them |
| OR-04 | Pay periods with no transactions and no transfers | Periods that have zero transactions and zero transfers (may indicate gaps in recurrence generation) |
| OR-05 | Transfer templates with no transfers generated | Transfer templates where `is_active = True` but no `budget.transfers` rows reference them |
| OR-06 | Savings goals for inactive accounts | Goals where `is_active = True` but the referenced account has `is_active = False` |

### Category 3: Balance Anomalies

Checks that flag potential issues in the anchor balance and projection system.

| Check ID | Description | SQL Logic |
|----------|-------------|-----------|
| BA-01 | Anchor balance set but no anchor period | Accounts where `current_anchor_balance IS NOT NULL` but `current_anchor_period_id IS NULL` (or vice versa) |
| BA-02 | Anchor period is beyond the last pay period | Accounts where `current_anchor_period_id` points to a period with a `period_index` higher than the maximum for that user |
| BA-03 | Pay period sequence gaps | Users where the `period_index` values in `budget.pay_periods` are not contiguous (e.g., 0, 1, 2, 4 -- missing 3) |
| BA-04 | Pay period date overlap | Pay periods for the same user where date ranges overlap (`start_date` of one falls between `start_date` and `end_date` of another) |
| BA-05 | Large anchor balance jumps | Consecutive entries in `budget.account_anchor_history` for the same account where the balance changes by more than 50% (configurable threshold) |

### Category 4: Data Consistency

Cross-table logical consistency checks.

| Check ID | Description | SQL Logic |
|----------|-------------|-----------|
| DC-01 | Transactions with status "done" or "received" but no actual_amount | Status indicates completion but `actual_amount IS NULL` |
| DC-02 | Transfer from_account equals to_account | Should be prevented by check constraint, but verify no violations exist |
| DC-03 | Account type-specific params mismatch | Accounts with `account_type.category = 'liability'` but no `mortgage_params` or `auto_loan_params`; accounts with `account_type.name = 'hysa'` but no `hysa_params` |
| DC-04 | Self-referential credit payback cycles | Transactions where `credit_payback_for_id` forms a chain longer than 1 (A pays back B pays back C) |
| DC-05 | Active templates for inactive accounts | Transaction templates where `is_active = True` but the referenced account has `is_active = False` |
| DC-06 | Duplicate non-deleted transactions per template/period/scenario | Should be prevented by partial unique index, but verify no violations (especially after a restore) |
| DC-07 | Users without user_settings | Every user in `auth.users` should have exactly one row in `auth.user_settings` |
| DC-08 | Users without a baseline scenario | Every user should have exactly one scenario with `is_baseline = True` |
| DC-09 | Salary deduction target accounts belong to same user | `paycheck_deductions.target_account_id` references an account owned by a different user than the salary profile owner |

### Summary

| Category | Check Count | Severity |
|----------|-------------|----------|
| Referential Integrity | 13 | Critical |
| Orphan Detection | 6 | Warning |
| Balance Anomalies | 5 | Warning |
| Data Consistency | 9 | Mixed (Critical: DC-01, DC-06, DC-07, DC-08; Warning: others) |
| **Total** | **33** | |

---

## Work Units

The implementation is organized into 5 work units. Each unit leaves the project in a working state. Dependencies between units are noted.

### Dependency Graph

```
WU-1: Backup Script (backup.sh)
  |
  v
WU-2: Retention Script (backup_retention.sh)
  |
  v
WU-3: Restore Script (restore.sh)
  |
  v
WU-4: Integrity Check Script (integrity_check.py)
  |
  v
WU-5: Verify Script (verify_backup.sh) + Runbook
```

WU-1 and WU-2 are tightly coupled (retention requires backups to exist). WU-3 is independent of WU-2 but depends on WU-1 (needs a backup file to restore). WU-4 is independent of WU-1 through WU-3 but is listed after WU-3 for logical ordering. WU-5 ties everything together and depends on WU-1, WU-3, and WU-4.

---

### WU-1: Backup Script

**Goal:** Create `scripts/backup.sh` that produces a compressed PostgreSQL dump with timestamped filename, copies to local and NAS destinations, and exits with appropriate status codes.

**Depends on:** None.

#### Files to Create

**`scripts/backup.sh`** -- Bash script for automated database backup.

```bash
#!/bin/bash
set -euo pipefail

# ── Configuration ────────────────────────────────────────────────
# All values can be overridden via environment variables.

BACKUP_LOCAL_DIR="${BACKUP_LOCAL_DIR:-/var/backups/shekel}"
BACKUP_NAS_DIR="${BACKUP_NAS_DIR:-/mnt/nas/backups/shekel}"
BACKUP_ENCRYPTION_PASSPHRASE="${BACKUP_ENCRYPTION_PASSPHRASE:-}"

# Docker container names.
DB_CONTAINER="${DB_CONTAINER:-shekel-db}"

# Database connection (used inside the db container).
PGUSER="${PGUSER:-shekel_user}"
PGDATABASE="${PGDATABASE:-shekel}"

# Timestamp format for filenames.
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILENAME="shekel_backup_${TIMESTAMP}.sql.gz"

# ── Functions ────────────────────────────────────────────────────

log() {
    # Structured log output: [YYYY-MM-DD HH:MM:SS] [LEVEL] message
    local level="$1"
    shift
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [${level}] $*"
}

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Create a compressed PostgreSQL backup of the Shekel database.

Options:
    --local-dir DIR     Local backup directory (default: /var/backups/shekel)
    --nas-dir DIR       NAS backup directory (default: /mnt/nas/backups/shekel)
    --no-nas            Skip NAS copy (local only)
    --encrypt           Force encryption (requires BACKUP_ENCRYPTION_PASSPHRASE)
    --help              Show this help message

Environment Variables:
    BACKUP_LOCAL_DIR              Local backup directory
    BACKUP_NAS_DIR                NAS backup directory
    BACKUP_ENCRYPTION_PASSPHRASE  GPG passphrase for encryption (optional)
    DB_CONTAINER                  Docker container name for PostgreSQL
    PGUSER                        PostgreSQL user
    PGDATABASE                    PostgreSQL database name
EOF
}

check_prerequisites() {
    # Verify docker is available.
    if ! command -v docker &>/dev/null; then
        log "ERROR" "docker command not found"
        exit 1
    fi

    # Verify the database container is running.
    if ! docker inspect --format='{{.State.Running}}' "${DB_CONTAINER}" 2>/dev/null | grep -q true; then
        log "ERROR" "Database container '${DB_CONTAINER}' is not running"
        exit 1
    fi

    # Create local backup directory if it does not exist.
    mkdir -p "${BACKUP_LOCAL_DIR}"
}

create_backup() {
    # Run pg_dump inside the database container, compress, and write to local dir.
    local local_path="${BACKUP_LOCAL_DIR}/${BACKUP_FILENAME}"

    log "INFO" "Starting backup: ${BACKUP_FILENAME}"
    log "INFO" "Database: ${PGDATABASE} | User: ${PGUSER} | Container: ${DB_CONTAINER}"

    # pg_dump with custom format options:
    #   --clean: include DROP statements before CREATE
    #   --if-exists: add IF EXISTS to DROP statements
    #   --no-owner: omit ownership commands (portable across environments)
    #   --no-privileges: omit GRANT/REVOKE (portable)
    #   --schema: dump only application schemas (not pg_catalog, information_schema)
    # Pipe through gzip for compression.
    docker exec "${DB_CONTAINER}" pg_dump \
        -U "${PGUSER}" \
        -d "${PGDATABASE}" \
        --clean \
        --if-exists \
        --no-owner \
        --no-privileges \
        --schema=public \
        --schema=ref \
        --schema=auth \
        --schema=budget \
        --schema=salary \
        --schema=system \
        | gzip > "${local_path}"

    # Verify the file was created and is not empty.
    if [[ ! -s "${local_path}" ]]; then
        log "ERROR" "Backup file is empty or was not created: ${local_path}"
        exit 1
    fi

    local size
    size=$(du -h "${local_path}" | cut -f1)
    log "INFO" "Local backup created: ${local_path} (${size})"
}

encrypt_backup() {
    # Optionally encrypt the backup file with GPG symmetric encryption.
    if [[ -z "${BACKUP_ENCRYPTION_PASSPHRASE}" ]]; then
        return 0
    fi

    local local_path="${BACKUP_LOCAL_DIR}/${BACKUP_FILENAME}"
    local encrypted_path="${local_path}.gpg"

    log "INFO" "Encrypting backup with AES-256..."
    echo "${BACKUP_ENCRYPTION_PASSPHRASE}" | gpg --batch --yes --passphrase-fd 0 \
        --symmetric --cipher-algo AES256 \
        --output "${encrypted_path}" \
        "${local_path}"

    # Remove the unencrypted file.
    rm -f "${local_path}"

    # Update the filename to include .gpg extension.
    BACKUP_FILENAME="${BACKUP_FILENAME}.gpg"
    log "INFO" "Encrypted backup: ${encrypted_path}"
}

copy_to_nas() {
    # Copy the backup file to the NAS mount point.
    # Returns 0 on success, 1 on failure (non-fatal -- local backup already exists).
    local local_path="${BACKUP_LOCAL_DIR}/${BACKUP_FILENAME}"
    local nas_path="${BACKUP_NAS_DIR}/${BACKUP_FILENAME}"

    # Check if NAS is mounted and accessible.
    if [[ ! -d "${BACKUP_NAS_DIR}" ]]; then
        log "WARNING" "NAS directory does not exist: ${BACKUP_NAS_DIR}"
        return 1
    fi

    if ! touch "${BACKUP_NAS_DIR}/.backup_test" 2>/dev/null; then
        log "WARNING" "NAS directory is not writable: ${BACKUP_NAS_DIR}"
        rm -f "${BACKUP_NAS_DIR}/.backup_test" 2>/dev/null
        return 1
    fi
    rm -f "${BACKUP_NAS_DIR}/.backup_test"

    cp "${local_path}" "${nas_path}"
    log "INFO" "NAS copy complete: ${nas_path}"
    return 0
}

# ── Main ─────────────────────────────────────────────────────────

main() {
    local skip_nas=false

    # Parse command-line arguments.
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --local-dir)  BACKUP_LOCAL_DIR="$2"; shift 2 ;;
            --nas-dir)    BACKUP_NAS_DIR="$2"; shift 2 ;;
            --no-nas)     skip_nas=true; shift ;;
            --encrypt)
                if [[ -z "${BACKUP_ENCRYPTION_PASSPHRASE}" ]]; then
                    log "ERROR" "--encrypt requires BACKUP_ENCRYPTION_PASSPHRASE to be set"
                    exit 1
                fi
                shift ;;
            --help)       usage; exit 0 ;;
            *)            log "ERROR" "Unknown option: $1"; usage; exit 1 ;;
        esac
    done

    check_prerequisites

    # Create the backup.
    create_backup

    # Encrypt if passphrase is set.
    encrypt_backup

    # Copy to NAS (non-fatal on failure).
    local nas_status=0
    if [[ "${skip_nas}" == false ]]; then
        copy_to_nas || nas_status=1
    else
        log "INFO" "NAS copy skipped (--no-nas)"
    fi

    # Final status.
    if [[ ${nas_status} -eq 0 ]]; then
        log "INFO" "Backup complete: ${BACKUP_FILENAME}"
        exit 0
    else
        log "WARNING" "Backup complete (local only). NAS copy failed."
        # Exit 0 because the local backup succeeded.
        # The NAS failure is logged as a warning.
        # Monitoring should alert on WARNING-level log entries.
        exit 0
    fi
}

main "$@"
```

**Key design decisions:**

1. `pg_dump` runs inside the `shekel-db` container via `docker exec` to ensure PostgreSQL client/server version compatibility.
2. The `--schema` flags explicitly list all 6 schemas (`public`, `ref`, `auth`, `budget`, `salary`, `system`) to capture the `alembic_version` table in `public` and exclude PostgreSQL internal schemas.
3. `--clean --if-exists` produces a dump that can be restored to an existing database by dropping and recreating objects.
4. `--no-owner --no-privileges` makes the dump portable across environments where the PostgreSQL user may differ.
5. NAS failure is non-fatal: the script logs a warning but exits 0 if the local backup succeeded. This satisfies Risk R7 from the master plan.
6. Encryption is opt-in via `BACKUP_ENCRYPTION_PASSPHRASE`. When set, the `.sql.gz` is encrypted to `.sql.gz.gpg` and the unencrypted file is removed.

#### Test Gate

- [ ] `scripts/backup.sh --help` prints usage and exits 0
- [ ] `scripts/backup.sh --no-nas --local-dir /tmp/shekel_test_backup` creates a `.sql.gz` file in the specified directory
- [ ] The backup file contains SQL statements for all 5 schemas + public
- [ ] `zcat <backup_file> | head -50` shows `pg_dump` header and schema creation statements
- [ ] Script exits non-zero if the database container is not running
- [ ] Script exits 0 with a WARNING log if NAS directory is unreachable

#### Manual Verification

1. **Happy path:**
   ```bash
   mkdir -p /tmp/shekel_test_backup
   ./scripts/backup.sh --no-nas --local-dir /tmp/shekel_test_backup
   # Verify: file exists, is non-empty, contains valid SQL
   ls -lh /tmp/shekel_test_backup/shekel_backup_*.sql.gz
   zcat /tmp/shekel_test_backup/shekel_backup_*.sql.gz | head -20
   ```

2. **Encryption:**
   ```bash
   BACKUP_ENCRYPTION_PASSPHRASE="test123" ./scripts/backup.sh --no-nas --local-dir /tmp/shekel_test_backup
   # Verify: .sql.gz.gpg file exists, .sql.gz does not
   ls /tmp/shekel_test_backup/shekel_backup_*.sql.gz.gpg
   # Decrypt and verify:
   echo "test123" | gpg --batch --passphrase-fd 0 -d /tmp/shekel_test_backup/shekel_backup_*.sql.gz.gpg | zcat | head -20
   ```

3. **NAS failure:**
   ```bash
   ./scripts/backup.sh --local-dir /tmp/shekel_test_backup --nas-dir /nonexistent/path
   # Verify: exit code is 0, log contains WARNING about NAS
   echo $?
   ```

4. **Database container not running:**
   ```bash
   docker stop shekel-db
   ./scripts/backup.sh --no-nas --local-dir /tmp/shekel_test_backup
   # Verify: exit code is non-zero, log contains ERROR
   echo $?
   docker start shekel-db
   ```

---

### WU-2: Retention Script

**Goal:** Create `scripts/backup_retention.sh` that prunes old backup files according to tiered retention policy (daily: 7 days, weekly/Sunday: 4 weeks, monthly/1st: 6 months). Applies independently to local and NAS directories.

**Depends on:** WU-1 (backup files must exist to be pruned; also establishes the filename convention `shekel_backup_YYYYMMDD_HHMMSS.sql.gz[.gpg]`).

#### Files to Create

**`scripts/backup_retention.sh`** -- Bash script for tiered backup retention pruning.

```bash
#!/bin/bash
set -euo pipefail

# ── Configuration ────────────────────────────────────────────────
# All values can be overridden via environment variables.

BACKUP_LOCAL_DIR="${BACKUP_LOCAL_DIR:-/var/backups/shekel}"
BACKUP_NAS_DIR="${BACKUP_NAS_DIR:-/mnt/nas/backups/shekel}"

# Retention periods.
RETENTION_DAILY_DAYS="${RETENTION_DAILY_DAYS:-7}"
RETENTION_WEEKLY_WEEKS="${RETENTION_WEEKLY_WEEKS:-4}"
RETENTION_MONTHLY_MONTHS="${RETENTION_MONTHLY_MONTHS:-6}"

# ── Functions ────────────────────────────────────────────────────

log() {
    local level="$1"
    shift
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [${level}] $*"
}

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Prune old Shekel backup files according to tiered retention policy.

Retention tiers:
    Daily backups:   kept for ${RETENTION_DAILY_DAYS} days
    Weekly (Sunday): kept for ${RETENTION_WEEKLY_WEEKS} weeks
    Monthly (1st):   kept for ${RETENTION_MONTHLY_MONTHS} months

Options:
    --local-dir DIR     Local backup directory (default: /var/backups/shekel)
    --nas-dir DIR       NAS backup directory (default: /mnt/nas/backups/shekel)
    --dry-run           Print what would be deleted without deleting
    --help              Show this help message

Environment Variables:
    BACKUP_LOCAL_DIR          Local backup directory
    BACKUP_NAS_DIR            NAS backup directory
    RETENTION_DAILY_DAYS      Days to keep daily backups (default: 7)
    RETENTION_WEEKLY_WEEKS    Weeks to keep weekly/Sunday backups (default: 4)
    RETENTION_MONTHLY_MONTHS  Months to keep monthly/1st backups (default: 6)
EOF
}

extract_date_from_filename() {
    # Extract YYYYMMDD from shekel_backup_YYYYMMDD_HHMMSS.sql.gz[.gpg]
    local filename="$1"
    echo "${filename}" | grep -oP 'shekel_backup_\K\d{8}'
}

is_sunday_backup() {
    # Check if the backup date (YYYYMMDD) falls on a Sunday.
    local date_str="$1"  # YYYYMMDD
    local formatted="${date_str:0:4}-${date_str:4:2}-${date_str:6:2}"
    local dow
    dow=$(date -d "${formatted}" +%u 2>/dev/null) || return 1
    [[ "${dow}" -eq 7 ]]
}

is_first_of_month_backup() {
    # Check if the backup date (YYYYMMDD) is the 1st of the month.
    local date_str="$1"  # YYYYMMDD
    [[ "${date_str:6:2}" == "01" ]]
}

days_old() {
    # Calculate how many days old a backup is based on the date in its filename.
    local date_str="$1"  # YYYYMMDD
    local formatted="${date_str:0:4}-${date_str:4:2}-${date_str:6:2}"
    local backup_epoch today_epoch
    backup_epoch=$(date -d "${formatted}" +%s 2>/dev/null) || return 1
    today_epoch=$(date +%s)
    echo $(( (today_epoch - backup_epoch) / 86400 ))
}

prune_directory() {
    # Apply retention policy to a single directory.
    local dir="$1"
    local dry_run="$2"
    local pruned=0

    if [[ ! -d "${dir}" ]]; then
        log "WARNING" "Directory does not exist, skipping: ${dir}"
        return 0
    fi

    log "INFO" "Processing directory: ${dir}"

    # Calculate cutoff thresholds.
    local weekly_cutoff_days=$(( RETENTION_WEEKLY_WEEKS * 7 ))
    local monthly_cutoff_days=$(( RETENTION_MONTHLY_MONTHS * 30 ))

    # Iterate over backup files in the directory.
    for filepath in "${dir}"/shekel_backup_*.sql.gz*; do
        [[ -f "${filepath}" ]] || continue

        local filename
        filename=$(basename "${filepath}")
        local date_str
        date_str=$(extract_date_from_filename "${filename}") || continue
        local age
        age=$(days_old "${date_str}") || continue

        local keep=false
        local tier=""

        # Monthly tier: 1st of month, kept for RETENTION_MONTHLY_MONTHS months.
        if is_first_of_month_backup "${date_str}" && [[ ${age} -le ${monthly_cutoff_days} ]]; then
            keep=true
            tier="monthly"
        fi

        # Weekly tier: Sunday backups, kept for RETENTION_WEEKLY_WEEKS weeks.
        if [[ "${keep}" == false ]] && is_sunday_backup "${date_str}" && [[ ${age} -le ${weekly_cutoff_days} ]]; then
            keep=true
            tier="weekly"
        fi

        # Daily tier: all backups within RETENTION_DAILY_DAYS.
        if [[ "${keep}" == false ]] && [[ ${age} -le ${RETENTION_DAILY_DAYS} ]]; then
            keep=true
            tier="daily"
        fi

        if [[ "${keep}" == false ]]; then
            if [[ "${dry_run}" == true ]]; then
                log "INFO" "[DRY RUN] Would delete: ${filename} (age: ${age}d)"
            else
                rm -f "${filepath}"
                log "INFO" "Deleted: ${filename} (age: ${age}d)"
            fi
            pruned=$((pruned + 1))
        fi
    done

    if [[ ${pruned} -eq 0 ]]; then
        log "INFO" "No files to prune in ${dir}"
    else
        local verb="Pruned"
        [[ "${dry_run}" == true ]] && verb="Would prune"
        log "INFO" "${verb} ${pruned} file(s) from ${dir}"
    fi
}

# ── Main ─────────────────────────────────────────────────────────

main() {
    local dry_run=false

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --local-dir)  BACKUP_LOCAL_DIR="$2"; shift 2 ;;
            --nas-dir)    BACKUP_NAS_DIR="$2"; shift 2 ;;
            --dry-run)    dry_run=true; shift ;;
            --help)       usage; exit 0 ;;
            *)            log "ERROR" "Unknown option: $1"; usage; exit 1 ;;
        esac
    done

    log "INFO" "Retention policy: daily=${RETENTION_DAILY_DAYS}d, weekly=${RETENTION_WEEKLY_WEEKS}w, monthly=${RETENTION_MONTHLY_MONTHS}m"

    # Process both directories independently.
    prune_directory "${BACKUP_LOCAL_DIR}" "${dry_run}"
    prune_directory "${BACKUP_NAS_DIR}" "${dry_run}"

    log "INFO" "Retention cleanup complete"
}

main "$@"
```

**Key design decisions:**

1. Retention tiers are evaluated in order: monthly > weekly > daily. A file that qualifies for a higher tier is always kept.
2. The date is extracted from the **filename** (`shekel_backup_YYYYMMDD_HHMMSS`), not the file modification time, as specified in the constraints.
3. "Sunday" is determined using GNU `date`'s day-of-week calculation. "1st of month" is a simple string check on the DD portion.
4. Local and NAS directories are pruned independently -- a file can be pruned from local but retained on NAS if the NAS has a different set of files.
5. `--dry-run` mode logs what would be deleted without deleting.
6. Both `.sql.gz` and `.sql.gz.gpg` files are handled by the glob `shekel_backup_*.sql.gz*`.

#### Test Gate

- [ ] `scripts/backup_retention.sh --help` prints usage and exits 0
- [ ] `--dry-run` logs files that would be deleted without removing them
- [ ] Daily backups older than 7 days are deleted (unless they qualify for weekly or monthly tiers)
- [ ] Sunday backups older than 4 weeks but younger than 6 months are deleted
- [ ] 1st-of-month backups older than 6 months are deleted
- [ ] A backup from 3 days ago is retained (daily tier)
- [ ] A backup from Sunday 2 weeks ago is retained (weekly tier)
- [ ] A backup from the 1st of last month is retained (monthly tier)
- [ ] Missing NAS directory produces a WARNING but does not fail the script

#### Manual Verification

Create test backup files with various dates and verify the retention logic:

```bash
# Setup: create a test directory with fake backup files.
TEST_DIR=$(mktemp -d)
mkdir -p "${TEST_DIR}/local"

# Create files with various dates (modify the timestamp in the filename).
# Today: should be kept (daily).
touch "${TEST_DIR}/local/shekel_backup_$(date +%Y%m%d)_020000.sql.gz"

# 3 days ago: should be kept (daily).
touch "${TEST_DIR}/local/shekel_backup_$(date -d '-3 days' +%Y%m%d)_020000.sql.gz"

# 10 days ago, not Sunday, not 1st: should be deleted.
touch "${TEST_DIR}/local/shekel_backup_$(date -d '-10 days' +%Y%m%d)_020000.sql.gz"

# Find the most recent Sunday within 3 weeks: should be kept (weekly).
RECENT_SUNDAY=$(date -d "last Sunday - 14 days" +%Y%m%d)
touch "${TEST_DIR}/local/shekel_backup_${RECENT_SUNDAY}_020000.sql.gz"

# 1st of two months ago: should be kept (monthly).
FIRST_OF_MONTH=$(date -d "2 months ago" +%Y%m)01
touch "${TEST_DIR}/local/shekel_backup_${FIRST_OF_MONTH}_020000.sql.gz"

# 1st of eight months ago: should be deleted (older than 6 months).
OLD_FIRST=$(date -d "8 months ago" +%Y%m)01
touch "${TEST_DIR}/local/shekel_backup_${OLD_FIRST}_020000.sql.gz"

# Dry run to verify.
./scripts/backup_retention.sh --local-dir "${TEST_DIR}/local" --nas-dir /nonexistent --dry-run

# Actual run.
./scripts/backup_retention.sh --local-dir "${TEST_DIR}/local" --nas-dir /nonexistent

# Verify remaining files.
ls "${TEST_DIR}/local/"

# Cleanup.
rm -rf "${TEST_DIR}"
```

---

### WU-3: Restore Script

**Goal:** Create `scripts/restore.sh` that restores a Shekel database from a backup file with interactive confirmation, database recreation, Alembic migration, and app container restart.

**Depends on:** WU-1 (uses backup files produced by `backup.sh`; follows the same filename and encryption conventions).

#### Files to Create

**`scripts/restore.sh`** -- Bash script for database restoration from a backup file.

```bash
#!/bin/bash
set -euo pipefail

# ── Configuration ────────────────────────────────────────────────

DB_CONTAINER="${DB_CONTAINER:-shekel-db}"
APP_CONTAINER="${APP_CONTAINER:-shekel-app}"
PGUSER="${PGUSER:-shekel_user}"
PGDATABASE="${PGDATABASE:-shekel}"
BACKUP_ENCRYPTION_PASSPHRASE="${BACKUP_ENCRYPTION_PASSPHRASE:-}"

# ── Functions ────────────────────────────────────────────────────

log() {
    local level="$1"
    shift
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [${level}] $*"
}

usage() {
    cat <<EOF
Usage: $(basename "$0") <backup_file>

Restore the Shekel database from a backup file.

This will:
    1. Stop the application container
    2. Drop and recreate the database
    3. Restore from the backup file
    4. Run pending Alembic migrations
    5. Restart the application container

Arguments:
    backup_file     Path to a .sql.gz or .sql.gz.gpg backup file

Options:
    --skip-confirm  Skip the interactive confirmation prompt
    --help          Show this help message

Environment Variables:
    DB_CONTAINER                  Docker container name for PostgreSQL
    APP_CONTAINER                 Docker container name for the app
    PGUSER                        PostgreSQL user
    PGDATABASE                    PostgreSQL database name
    BACKUP_ENCRYPTION_PASSPHRASE  GPG passphrase (required for .gpg files)
EOF
}

confirm_restore() {
    # Interactive confirmation with default No.
    echo ""
    echo "============================================================"
    echo "  WARNING: This will REPLACE ALL DATA in the Shekel database"
    echo "============================================================"
    echo ""
    echo "  Backup file:  ${BACKUP_FILE}"
    echo "  Database:     ${PGDATABASE}"
    echo "  DB container: ${DB_CONTAINER}"
    echo ""
    read -r -p "  Are you sure you want to continue? [y/N] " response
    case "${response}" in
        [yY][eE][sS]|[yY])
            log "INFO" "Restore confirmed by user"
            ;;
        *)
            log "INFO" "Restore cancelled by user"
            exit 0
            ;;
    esac
}

stop_app() {
    log "INFO" "Stopping application container: ${APP_CONTAINER}"
    docker stop "${APP_CONTAINER}" 2>/dev/null || true
    log "INFO" "Application container stopped"
}

drop_and_recreate_database() {
    log "INFO" "Dropping and recreating database: ${PGDATABASE}"

    # Terminate existing connections to the target database.
    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d postgres -c \
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${PGDATABASE}' AND pid <> pg_backend_pid();" \
        >/dev/null 2>&1 || true

    # Drop and recreate the database.
    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d postgres -c \
        "DROP DATABASE IF EXISTS ${PGDATABASE};"
    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d postgres -c \
        "CREATE DATABASE ${PGDATABASE} OWNER ${PGUSER};"

    # Recreate schemas (pg_dump --clean does not create schemas themselves).
    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${PGDATABASE}" -c \
        "CREATE SCHEMA IF NOT EXISTS ref;
         CREATE SCHEMA IF NOT EXISTS auth;
         CREATE SCHEMA IF NOT EXISTS budget;
         CREATE SCHEMA IF NOT EXISTS salary;
         CREATE SCHEMA IF NOT EXISTS system;"

    log "INFO" "Database recreated with schemas"
}

restore_backup() {
    local backup_file="$1"
    local restore_input=""

    log "INFO" "Restoring from: ${backup_file}"

    # Determine if the file is encrypted.
    if [[ "${backup_file}" == *.gpg ]]; then
        if [[ -z "${BACKUP_ENCRYPTION_PASSPHRASE}" ]]; then
            log "ERROR" "Backup is encrypted but BACKUP_ENCRYPTION_PASSPHRASE is not set"
            exit 1
        fi
        log "INFO" "Decrypting backup..."
        # Decrypt and decompress in a pipeline, pipe to psql.
        echo "${BACKUP_ENCRYPTION_PASSPHRASE}" | gpg --batch --passphrase-fd 0 --quiet -d "${backup_file}" \
            | gunzip \
            | docker exec -i "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${PGDATABASE}" --quiet --single-transaction
    else
        # Decompress and pipe to psql.
        gunzip -c "${backup_file}" \
            | docker exec -i "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${PGDATABASE}" --quiet --single-transaction
    fi

    log "INFO" "Database restore complete"
}

run_migrations() {
    # Start the app container temporarily to run migrations, then stop it.
    # The entrypoint handles migration via init_database.py.
    # However, we only want to run migrations, not start the full app.
    # Use docker exec with a one-off command.
    log "INFO" "Running pending Alembic migrations..."

    # Start the app container so its environment is available.
    docker start "${APP_CONTAINER}"

    # Wait for the entrypoint to complete initialization (it runs migrations).
    # The entrypoint exits into gunicorn, so we wait for the container to be healthy.
    local retries=30
    while [[ ${retries} -gt 0 ]]; do
        local status
        status=$(docker inspect --format='{{.State.Health.Status}}' "${APP_CONTAINER}" 2>/dev/null || echo "none")
        if [[ "${status}" == "healthy" ]]; then
            log "INFO" "Application container is healthy"
            return 0
        fi
        # If the container has no health check, check if it's running.
        local running
        running=$(docker inspect --format='{{.State.Running}}' "${APP_CONTAINER}" 2>/dev/null || echo "false")
        if [[ "${running}" == "true" && "${status}" == "none" ]]; then
            # Wait a moment for the entrypoint to finish.
            sleep 2
            log "INFO" "Application container started (no health check configured)"
            return 0
        fi
        sleep 2
        retries=$((retries - 1))
    done

    log "WARNING" "Application container did not become healthy within 60s. Check logs: docker logs ${APP_CONTAINER}"
}

verify_restore() {
    # Quick sanity check: verify the database has data.
    local user_count
    user_count=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${PGDATABASE}" -t -c \
        "SELECT COUNT(*) FROM auth.users;" 2>/dev/null | tr -d ' ')

    if [[ -z "${user_count}" || "${user_count}" -eq 0 ]]; then
        log "WARNING" "No users found in the restored database. Verify the backup file."
    else
        log "INFO" "Verification: ${user_count} user(s) found in restored database"
    fi

    local period_count
    period_count=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${PGDATABASE}" -t -c \
        "SELECT COUNT(*) FROM budget.pay_periods;" 2>/dev/null | tr -d ' ')
    log "INFO" "Verification: ${period_count} pay period(s) in restored database"
}

# ── Main ─────────────────────────────────────────────────────────

main() {
    local skip_confirm=false
    local backup_file=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --skip-confirm) skip_confirm=true; shift ;;
            --help)         usage; exit 0 ;;
            -*)             log "ERROR" "Unknown option: $1"; usage; exit 1 ;;
            *)              backup_file="$1"; shift ;;
        esac
    done

    if [[ -z "${backup_file}" ]]; then
        log "ERROR" "No backup file specified"
        usage
        exit 1
    fi

    if [[ ! -f "${backup_file}" ]]; then
        log "ERROR" "Backup file not found: ${backup_file}"
        exit 1
    fi

    BACKUP_FILE="${backup_file}"

    # Confirmation prompt (default: No).
    if [[ "${skip_confirm}" == false ]]; then
        confirm_restore
    fi

    stop_app
    drop_and_recreate_database
    restore_backup "${backup_file}"
    run_migrations
    verify_restore

    log "INFO" "Restore complete. Application is running."
    log "INFO" "Review the application at http://localhost:5000 to verify."
}

main "$@"
```

**Key design decisions:**

1. **Interactive confirmation defaults to No** (`[y/N]`). Pressing Enter without typing "y" cancels the restore. The `--skip-confirm` flag is available for scripted use (e.g., from `verify_backup.sh`).
2. The restore sequence is: stop app → terminate connections → drop database → create database → create schemas → restore from dump → start app (which runs entrypoint including `init_database.py` for migrations).
3. The entrypoint handles Alembic migrations automatically on startup (`scripts/init_database.py`), so after restoring an older backup, the app's normal startup sequence applies any pending migrations.
4. `--single-transaction` flag on `psql` ensures the restore is atomic -- if any statement fails, the entire restore is rolled back.
5. Encrypted backups (`.gpg` extension) are automatically detected and decrypted using `BACKUP_ENCRYPTION_PASSPHRASE`.
6. A basic post-restore verification checks for users and pay periods to confirm the restore produced a non-empty database.

#### Test Gate

- [ ] `scripts/restore.sh --help` prints usage and exits 0
- [ ] Pressing Enter at the confirmation prompt cancels the restore
- [ ] Typing "y" at the confirmation proceeds with the restore
- [ ] `--skip-confirm` bypasses the prompt
- [ ] Specifying a nonexistent file exits with an error
- [ ] After restore, the database contains the expected data
- [ ] After restore, the application starts and is accessible

#### Manual Verification

1. **Happy path (full cycle):**
   ```bash
   # Create a backup first.
   mkdir -p /tmp/shekel_test_backup
   ./scripts/backup.sh --no-nas --local-dir /tmp/shekel_test_backup
   BACKUP_FILE=$(ls -t /tmp/shekel_test_backup/shekel_backup_*.sql.gz | head -1)

   # Restore from the backup.
   ./scripts/restore.sh "${BACKUP_FILE}"
   # Type "y" at the prompt.

   # Verify: application is accessible.
   curl -s http://localhost:5000/login | head -5

   # Verify: database has data.
   docker exec shekel-db psql -U shekel_user -d shekel -c "SELECT COUNT(*) FROM auth.users;"
   docker exec shekel-db psql -U shekel_user -d shekel -c "SELECT COUNT(*) FROM budget.pay_periods;"
   ```

2. **Confirmation default-No:**
   ```bash
   echo "" | ./scripts/restore.sh "${BACKUP_FILE}"
   # Verify: "Restore cancelled by user" in output, app still running normally
   ```

3. **Encrypted backup:**
   ```bash
   BACKUP_ENCRYPTION_PASSPHRASE="test123" ./scripts/backup.sh --no-nas --local-dir /tmp/shekel_test_backup
   ENC_FILE=$(ls -t /tmp/shekel_test_backup/shekel_backup_*.sql.gz.gpg | head -1)
   BACKUP_ENCRYPTION_PASSPHRASE="test123" ./scripts/restore.sh --skip-confirm "${ENC_FILE}"
   ```

---

### WU-4: Integrity Check Script

**Goal:** Create `scripts/integrity_check.py` that validates referential integrity, detects orphaned records, flags balance anomalies, and checks data consistency across all database schemas. Runnable both standalone (CLI) and importable (for `verify_backup.sh` and pytest).

**Depends on:** None (independent of shell scripts; uses the existing SQLAlchemy/Flask stack).

#### Files to Create

**`scripts/integrity_check.py`** -- Python script for database integrity validation.

```python
"""
Shekel Budget App -- Data Integrity Check

Validates referential integrity, detects orphaned records, flags balance
anomalies, and checks data consistency across all database schemas.

Designed to be:
    - Run standalone via CLI: python scripts/integrity_check.py
    - Called from verify_backup.sh against a temporary database
    - Tested by pytest against the test database

Usage:
    python scripts/integrity_check.py [--database-url URL] [--verbose] [--category CAT]

Options:
    --database-url URL   Override the database URL (for verify_backup.sh)
    --verbose            Print details for each check, not just failures
    --category CAT       Run only checks in this category
                         (referential, orphan, balance, consistency)

Exit codes:
    0   All checks passed
    1   One or more CRITICAL checks failed
    2   One or more WARNING checks flagged issues (no critical failures)
    3   Script error (bad arguments, database connection failure)

Cron example (weekly, after backup verification):
    0 3 * * 0 docker exec shekel-app python scripts/integrity_check.py
"""
```

**Module structure:**

```python
import argparse
import logging
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@dataclass
class CheckResult:
    """Result of a single integrity check.

    Attributes:
        check_id: Identifier like 'FK-01', 'OR-03', 'BA-02', 'DC-05'.
        category: One of 'referential', 'orphan', 'balance', 'consistency'.
        severity: 'critical' or 'warning'.
        description: Human-readable description of what was checked.
        passed: True if no issues found.
        detail_count: Number of violations found (0 if passed).
        details: List of dicts with violation specifics (e.g., row IDs).
    """
    check_id: str
    category: str
    severity: str
    description: str
    passed: bool
    detail_count: int = 0
    details: list = None

    def __post_init__(self):
        if self.details is None:
            self.details = []
```

**Core functions (all take a SQLAlchemy `session` argument for testability):**

```python
def run_all_checks(session, categories=None, verbose=False):
    """Execute all integrity checks against the given database session.

    Args:
        session: A SQLAlchemy session connected to the target database.
        categories: Optional list of category names to filter checks.
            Valid values: 'referential', 'orphan', 'balance', 'consistency'.
            If None, all categories are run.
        verbose: If True, log details for passing checks too.

    Returns:
        List of CheckResult objects, one per check executed.
    """
    # Implementation:
    # 1. Build list of check functions by category.
    # 2. Filter by categories if specified.
    # 3. Execute each check, collecting CheckResult objects.
    # 4. Log results as they complete.
    # 5. Return the full list.


def check_referential_integrity(session):
    """Run all FK-* referential integrity checks.

    Returns:
        List of CheckResult for checks FK-01 through FK-13.
    """
    # Implementation: For each FK check, execute a LEFT JOIN query
    # that finds rows where the FK column is NOT NULL but the referenced
    # row does not exist.
    #
    # Example SQL for FK-01 (accounts without valid user):
    #   SELECT a.id, a.name, a.user_id
    #   FROM budget.accounts a
    #   LEFT JOIN auth.users u ON a.user_id = u.id
    #   WHERE u.id IS NULL
    #
    # Each query returns a list of violating rows.
    # If the list is empty, the check passes.


def check_orphaned_records(session):
    """Run all OR-* orphan detection checks.

    Returns:
        List of CheckResult for checks OR-01 through OR-06.
    """
    # Implementation: For each orphan check, execute a query that
    # finds records not referenced by any parent.
    #
    # Example SQL for OR-02 (recurrence rules not referenced by any template):
    #   SELECT r.id
    #   FROM budget.recurrence_rules r
    #   LEFT JOIN budget.transaction_templates tt ON tt.recurrence_rule_id = r.id
    #   LEFT JOIN budget.transfer_templates tft ON tft.recurrence_rule_id = r.id
    #   WHERE tt.id IS NULL AND tft.id IS NULL


def check_balance_anomalies(session):
    """Run all BA-* balance anomaly checks.

    Returns:
        List of CheckResult for checks BA-01 through BA-05.
    """
    # Implementation: Queries that detect inconsistencies in the
    # anchor balance system and pay period sequences.
    #
    # BA-03 (pay period sequence gaps):
    #   WITH numbered AS (
    #       SELECT user_id, period_index,
    #              LAG(period_index) OVER (PARTITION BY user_id ORDER BY period_index) AS prev_idx
    #       FROM budget.pay_periods
    #   )
    #   SELECT user_id, prev_idx, period_index
    #   FROM numbered
    #   WHERE period_index - prev_idx > 1


def check_data_consistency(session):
    """Run all DC-* data consistency checks.

    Returns:
        List of CheckResult for checks DC-01 through DC-09.
    """
    # Implementation: Cross-table logical consistency queries.
    #
    # DC-07 (users without user_settings):
    #   SELECT u.id, u.email
    #   FROM auth.users u
    #   LEFT JOIN auth.user_settings s ON u.id = s.user_id
    #   WHERE s.id IS NULL


def summarize_results(results, verbose=False):
    """Log a summary of all check results.

    Args:
        results: List of CheckResult objects.
        verbose: If True, log details for each check.

    Returns:
        Tuple of (critical_failures, warnings) counts.
    """
    # Implementation:
    # 1. Group results by category.
    # 2. For each check, log PASS/FAIL with check_id and description.
    # 3. For failures, log detail_count and details.
    # 4. Print summary counts at the end.
    # 5. Return (critical_count, warning_count).


def parse_args(argv=None):
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        argparse.Namespace with database_url, verbose, and category.
    """


def run_cli(database_url=None, categories=None, verbose=False):
    """CLI entry point: create app, run checks, print results, exit.

    If database_url is provided, it overrides the Flask config.
    This allows verify_backup.sh to point at a temporary database.

    Args:
        database_url: Optional override for DATABASE_URL.
        categories: Optional list of categories to check.
        verbose: Print details for passing checks.

    Returns:
        Exit code (0, 1, 2, or 3).
    """
    # Implementation:
    # 1. Set DATABASE_URL env var if override provided.
    # 2. create_app() + app.app_context().
    # 3. Call run_all_checks(db.session, categories, verbose).
    # 4. Call summarize_results().
    # 5. Return appropriate exit code.
```

**CLI entry point:**

```python
if __name__ == "__main__":
    args = parse_args()
    code = run_cli(
        database_url=args.database_url,
        categories=[args.category] if args.category else None,
        verbose=args.verbose,
    )
    sys.exit(code)
```

**Implementation notes for each check:**

All SQL queries are executed via `session.execute(text(...))` with parameterized values where applicable. Each check function follows this pattern:

```python
def _check_fk_01(session):
    """FK-01: Accounts without a valid user."""
    result = session.execute(text("""
        SELECT a.id, a.name, a.user_id
        FROM budget.accounts a
        LEFT JOIN auth.users u ON a.user_id = u.id
        WHERE u.id IS NULL
    """))
    rows = result.fetchall()
    return CheckResult(
        check_id="FK-01",
        category="referential",
        severity="critical",
        description="Accounts without a valid user",
        passed=len(rows) == 0,
        detail_count=len(rows),
        details=[{"account_id": r.id, "name": r.name, "user_id": r.user_id} for r in rows],
    )
```

#### Files to Create (Test)

**`tests/test_scripts/test_integrity_check.py`** -- pytest tests for the integrity check script.

```python
"""Tests for scripts/integrity_check.py (Phase 8C WU-4)."""
from scripts.integrity_check import (
    run_all_checks,
    check_referential_integrity,
    check_orphaned_records,
    check_balance_anomalies,
    check_data_consistency,
    CheckResult,
)
```

Test classes and methods:

```python
class TestCheckResult:
    """Tests for the CheckResult dataclass."""

    def test_passing_check(self):
        """A passing CheckResult has passed=True and detail_count=0."""

    def test_failing_check(self):
        """A failing CheckResult has passed=False and detail_count > 0."""


class TestReferentialIntegrity:
    """Tests for FK-* referential integrity checks."""

    def test_clean_database_passes_all(self, app, db, seed_user, seed_periods):
        """All FK checks pass on a properly seeded database."""

    def test_fk01_detects_orphaned_account(self, app, db, seed_user):
        """FK-01 detects an account whose user_id references a nonexistent user."""
        # Insert an account with a bogus user_id via raw SQL (bypass FK).

    def test_fk05_detects_transaction_with_missing_period(self, app, db, seed_user, seed_periods):
        """FK-05 detects a transaction referencing a nonexistent pay period."""

    def test_fk10_detects_template_with_missing_category(self, app, db, seed_user):
        """FK-10 detects a transaction template with an invalid category_id."""


class TestOrphanDetection:
    """Tests for OR-* orphan detection checks."""

    def test_clean_database_no_orphans(self, app, db, seed_user, seed_periods):
        """No orphans detected on a properly seeded database."""

    def test_or02_detects_unused_recurrence_rule(self, app, db, seed_user):
        """OR-02 detects a recurrence rule not referenced by any template."""

    def test_or03_detects_unused_category(self, app, db, seed_user):
        """OR-03 detects a category not used by any template or transaction."""

    def test_or06_detects_goal_on_inactive_account(self, app, db, seed_user):
        """OR-06 flags a savings goal on an inactive account."""


class TestBalanceAnomalies:
    """Tests for BA-* balance anomaly checks."""

    def test_clean_database_no_anomalies(self, app, db, seed_user, seed_periods):
        """No balance anomalies on a properly seeded database."""

    def test_ba01_detects_balance_without_period(self, app, db, seed_user):
        """BA-01 flags account with anchor balance but no anchor period."""

    def test_ba03_detects_period_gap(self, app, db, seed_user):
        """BA-03 detects a gap in the pay period index sequence."""

    def test_ba04_detects_date_overlap(self, app, db, seed_user):
        """BA-04 detects overlapping pay period date ranges."""


class TestDataConsistency:
    """Tests for DC-* data consistency checks."""

    def test_clean_database_passes(self, app, db, seed_user, seed_periods):
        """All consistency checks pass on a properly seeded database."""

    def test_dc01_detects_done_without_actual(self, app, db, seed_user, seed_periods):
        """DC-01 flags a transaction with status 'done' but no actual_amount."""

    def test_dc05_detects_active_template_inactive_account(self, app, db, seed_user):
        """DC-05 flags an active template referencing an inactive account."""

    def test_dc07_detects_user_without_settings(self, app, db):
        """DC-07 detects a user without a user_settings row."""

    def test_dc08_detects_user_without_baseline(self, app, db, seed_user):
        """DC-08 detects a user without a baseline scenario."""

    def test_dc09_detects_cross_user_deduction_target(self, app, db, seed_user):
        """DC-09 flags a deduction targeting another user's account."""


class TestRunAllChecks:
    """Tests for the top-level run_all_checks() function."""

    def test_runs_all_categories_by_default(self, app, db, seed_user, seed_periods):
        """run_all_checks() returns results from all 4 categories."""

    def test_category_filter(self, app, db, seed_user, seed_periods):
        """run_all_checks(categories=['referential']) only runs FK checks."""

    def test_returns_check_result_objects(self, app, db, seed_user, seed_periods):
        """All returned items are CheckResult instances."""

    def test_exit_code_zero_on_clean_db(self, app, db, seed_user, seed_periods):
        """No critical or warning failures on a properly seeded database."""
```

#### Test Gate

- [ ] `python scripts/integrity_check.py --help` prints usage and exits 0
- [ ] `pytest tests/test_scripts/test_integrity_check.py -v` passes all tests
- [ ] All 33 checks pass on a properly seeded development database
- [ ] Script exits 1 when a critical check fails
- [ ] Script exits 2 when only warning checks fail
- [ ] `--category referential` runs only FK checks
- [ ] `--database-url` override connects to a different database

#### Impact on Existing Tests

No existing tests are affected. The integrity check script is entirely new and does not modify any application code, models, or routes. The test file `tests/test_scripts/test_integrity_check.py` uses the same fixtures (`app`, `db`, `seed_user`, `seed_periods`) as existing script tests.

Some tests intentionally create invalid data (e.g., orphaned records) by using raw SQL to bypass SQLAlchemy FK constraints. These tests operate within a single test transaction and are cleaned up by the `db` fixture's truncation between tests.

---

### WU-5: Verify Script and Runbook

**Goal:** Create `scripts/verify_backup.sh` that restores a backup to a temporary database, runs integrity checks and sanity queries, and cleans up. Create the `docs/backup_runbook.md` documentation covering all backup, restore, retention, and verification procedures.

**Depends on:** WU-1 (backup files), WU-3 (restore logic reused conceptually), WU-4 (integrity check script called during verification).

#### Files to Create

**`scripts/verify_backup.sh`** -- Bash script for backup verification against a temporary database.

```bash
#!/bin/bash
set -euo pipefail

# ── Configuration ────────────────────────────────────────────────

DB_CONTAINER="${DB_CONTAINER:-shekel-db}"
APP_CONTAINER="${APP_CONTAINER:-shekel-app}"
PGUSER="${PGUSER:-shekel_user}"
PGDATABASE="${PGDATABASE:-shekel}"
VERIFY_DB="${VERIFY_DB:-shekel_verify}"
BACKUP_ENCRYPTION_PASSPHRASE="${BACKUP_ENCRYPTION_PASSPHRASE:-}"

# ── Functions ────────────────────────────────────────────────────

log() {
    local level="$1"
    shift
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [${level}] $*"
}

usage() {
    cat <<EOF
Usage: $(basename "$0") <backup_file>

Verify a Shekel backup by restoring to a temporary database and running
sanity checks.

This will:
    1. Create a temporary database (${VERIFY_DB})
    2. Restore the backup into it
    3. Run sanity check queries (row counts, user check, date ranges)
    4. Run the integrity check script
    5. Drop the temporary database

The production database is never touched.

Arguments:
    backup_file     Path to a .sql.gz or .sql.gz.gpg backup file

Options:
    --help          Show this help message

Environment Variables:
    DB_CONTAINER                  Docker container name for PostgreSQL
    APP_CONTAINER                 Docker container name for the app
    PGUSER                        PostgreSQL user
    PGDATABASE                    Production database name (for reference)
    VERIFY_DB                     Temporary database name for verification
    BACKUP_ENCRYPTION_PASSPHRASE  GPG passphrase (required for .gpg files)
EOF
}

cleanup() {
    # Trap handler: always drop the temporary database on exit.
    log "INFO" "Cleaning up: dropping temporary database ${VERIFY_DB}"
    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d postgres -c \
        "DROP DATABASE IF EXISTS ${VERIFY_DB};" 2>/dev/null || true
}

create_temp_database() {
    log "INFO" "Creating temporary database: ${VERIFY_DB}"

    # Drop if it exists from a previous failed run.
    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d postgres -c \
        "DROP DATABASE IF EXISTS ${VERIFY_DB};" 2>/dev/null || true

    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d postgres -c \
        "CREATE DATABASE ${VERIFY_DB} OWNER ${PGUSER};"

    # Create schemas.
    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${VERIFY_DB}" -c \
        "CREATE SCHEMA IF NOT EXISTS ref;
         CREATE SCHEMA IF NOT EXISTS auth;
         CREATE SCHEMA IF NOT EXISTS budget;
         CREATE SCHEMA IF NOT EXISTS salary;
         CREATE SCHEMA IF NOT EXISTS system;"

    log "INFO" "Temporary database created"
}

restore_to_temp() {
    local backup_file="$1"
    log "INFO" "Restoring backup to temporary database..."

    if [[ "${backup_file}" == *.gpg ]]; then
        if [[ -z "${BACKUP_ENCRYPTION_PASSPHRASE}" ]]; then
            log "ERROR" "Backup is encrypted but BACKUP_ENCRYPTION_PASSPHRASE is not set"
            return 1
        fi
        echo "${BACKUP_ENCRYPTION_PASSPHRASE}" | gpg --batch --passphrase-fd 0 --quiet -d "${backup_file}" \
            | gunzip \
            | docker exec -i "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${VERIFY_DB}" --quiet --single-transaction
    else
        gunzip -c "${backup_file}" \
            | docker exec -i "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${VERIFY_DB}" --quiet --single-transaction
    fi

    log "INFO" "Restore to temporary database complete"
}

run_sanity_checks() {
    # Sanity queries against the temporary database.
    local failures=0

    log "INFO" "Running sanity checks..."

    # Check 1: auth.users has at least one row.
    local user_count
    user_count=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${VERIFY_DB}" -t -c \
        "SELECT COUNT(*) FROM auth.users;" | tr -d ' ')
    if [[ "${user_count}" -gt 0 ]]; then
        log "INFO" "  [PASS] auth.users: ${user_count} user(s)"
    else
        log "ERROR" "  [FAIL] auth.users: 0 users"
        failures=$((failures + 1))
    fi

    # Check 2: budget.pay_periods has rows and spans a reasonable date range.
    local period_info
    period_info=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${VERIFY_DB}" -t -c \
        "SELECT COUNT(*), MIN(start_date), MAX(end_date) FROM budget.pay_periods;" | tr -d ' ')
    local period_count min_date max_date
    period_count=$(echo "${period_info}" | cut -d'|' -f1)
    min_date=$(echo "${period_info}" | cut -d'|' -f2)
    max_date=$(echo "${period_info}" | cut -d'|' -f3)
    if [[ "${period_count}" -gt 0 ]]; then
        log "INFO" "  [PASS] budget.pay_periods: ${period_count} periods (${min_date} to ${max_date})"
    else
        log "ERROR" "  [FAIL] budget.pay_periods: 0 periods"
        failures=$((failures + 1))
    fi

    # Check 3: budget.transactions has rows.
    local txn_count
    txn_count=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${VERIFY_DB}" -t -c \
        "SELECT COUNT(*) FROM budget.transactions;" | tr -d ' ')
    log "INFO" "  [INFO] budget.transactions: ${txn_count} row(s)"

    # Check 4: budget.accounts has rows.
    local acct_count
    acct_count=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${VERIFY_DB}" -t -c \
        "SELECT COUNT(*) FROM budget.accounts;" | tr -d ' ')
    if [[ "${acct_count}" -gt 0 ]]; then
        log "INFO" "  [PASS] budget.accounts: ${acct_count} account(s)"
    else
        log "ERROR" "  [FAIL] budget.accounts: 0 accounts"
        failures=$((failures + 1))
    fi

    # Check 5: ref tables are populated.
    local ref_count
    ref_count=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${VERIFY_DB}" -t -c \
        "SELECT COUNT(*) FROM ref.account_types;" | tr -d ' ')
    if [[ "${ref_count}" -gt 0 ]]; then
        log "INFO" "  [PASS] ref.account_types: ${ref_count} type(s)"
    else
        log "ERROR" "  [FAIL] ref.account_types: 0 types (reference data missing)"
        failures=$((failures + 1))
    fi

    # Check 6: system.audit_log table exists (may have 0 rows, that's OK).
    local audit_exists
    audit_exists=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${VERIFY_DB}" -t -c \
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'system' AND table_name = 'audit_log');" | tr -d ' ')
    if [[ "${audit_exists}" == "t" ]]; then
        local audit_count
        audit_count=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${VERIFY_DB}" -t -c \
            "SELECT COUNT(*) FROM system.audit_log;" | tr -d ' ')
        log "INFO" "  [PASS] system.audit_log: table exists (${audit_count} row(s))"
    else
        log "WARNING" "  [WARN] system.audit_log: table does not exist"
    fi

    # Check 7: alembic_version exists and has a value.
    local alembic_version
    alembic_version=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${VERIFY_DB}" -t -c \
        "SELECT version_num FROM public.alembic_version LIMIT 1;" 2>/dev/null | tr -d ' ') || true
    if [[ -n "${alembic_version}" ]]; then
        log "INFO" "  [PASS] alembic_version: ${alembic_version}"
    else
        log "WARNING" "  [WARN] alembic_version: not found (may indicate pre-migration backup)"
    fi

    return ${failures}
}

run_integrity_checks() {
    # Run the Python integrity check script against the temporary database.
    log "INFO" "Running integrity checks against temporary database..."

    local verify_url="postgresql://${PGUSER}:$(docker exec "${DB_CONTAINER}" printenv POSTGRES_PASSWORD 2>/dev/null || echo 'shekel_pass')@localhost:5432/${VERIFY_DB}"

    # The integrity check script runs inside the app container, but it needs
    # to connect to the verify database. We override DATABASE_URL.
    local exit_code=0
    docker exec -e "DATABASE_URL=postgresql://${PGUSER}:${DB_PASSWORD:-shekel_pass}@db:5432/${VERIFY_DB}" \
        "${APP_CONTAINER}" python scripts/integrity_check.py --verbose || exit_code=$?

    if [[ ${exit_code} -eq 0 ]]; then
        log "INFO" "Integrity checks: ALL PASSED"
    elif [[ ${exit_code} -eq 2 ]]; then
        log "WARNING" "Integrity checks: WARNINGS detected (no critical failures)"
    else
        log "ERROR" "Integrity checks: CRITICAL FAILURES detected"
    fi

    return ${exit_code}
}

# ── Main ─────────────────────────────────────────────────────────

main() {
    local backup_file=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --help) usage; exit 0 ;;
            -*)     log "ERROR" "Unknown option: $1"; usage; exit 1 ;;
            *)      backup_file="$1"; shift ;;
        esac
    done

    if [[ -z "${backup_file}" ]]; then
        log "ERROR" "No backup file specified"
        usage
        exit 1
    fi

    if [[ ! -f "${backup_file}" ]]; then
        log "ERROR" "Backup file not found: ${backup_file}"
        exit 1
    fi

    # Register cleanup trap.
    trap cleanup EXIT

    create_temp_database
    restore_to_temp "${backup_file}"

    local sanity_failures=0
    local integrity_code=0

    run_sanity_checks || sanity_failures=$?
    run_integrity_checks || integrity_code=$?

    # Report final status.
    echo ""
    log "INFO" "============================================================"
    log "INFO" "  BACKUP VERIFICATION SUMMARY"
    log "INFO" "============================================================"
    log "INFO" "  Backup file:     ${backup_file}"
    log "INFO" "  Sanity checks:   ${sanity_failures} failure(s)"
    log "INFO" "  Integrity checks: exit code ${integrity_code}"

    if [[ ${sanity_failures} -eq 0 && ${integrity_code} -eq 0 ]]; then
        log "INFO" "  Status: PASS"
        log "INFO" "============================================================"
        exit 0
    elif [[ ${integrity_code} -le 2 && ${sanity_failures} -eq 0 ]]; then
        log "WARNING" "  Status: PASS WITH WARNINGS"
        log "INFO" "============================================================"
        exit 2
    else
        log "ERROR" "  Status: FAIL"
        log "INFO" "============================================================"
        exit 1
    fi
}

main "$@"
```

**Key design decisions:**

1. A `trap cleanup EXIT` handler ensures the temporary database is dropped even if the script fails partway through.
2. The temporary database name (`shekel_verify`) is configurable via `VERIFY_DB` to avoid collisions.
3. Sanity checks (row counts, user existence, date ranges) are shell-level `psql` queries -- fast and independent of the Python stack.
4. The integrity check script runs inside the app container with an overridden `DATABASE_URL` pointing at the verify database.
5. The production database is never touched during verification.

---

**`docs/backup_runbook.md`** -- Comprehensive documentation for all backup and disaster recovery procedures.

Section outline:

```markdown
# Shekel Backup & Disaster Recovery Runbook

## 1. Overview
    - Purpose and scope
    - Backup strategy summary (daily automated, tiered retention, NAS copy)
    - Script inventory and locations

## 2. Prerequisites
    - Host: Arch Linux with Docker and Docker Compose
    - PostgreSQL container running as shekel-db
    - NAS mount configured (if using off-site backups)
    - Environment variables configured in .env or host shell

## 3. Automated Backup Setup
    ### 3.1 Cron Configuration
        - Daily backup crontab entry (2:00 AM):
          0 2 * * * /path/to/shekel/scripts/backup.sh >> /var/log/shekel_backup.log 2>&1
        - Daily retention cleanup (2:30 AM):
          30 2 * * * /path/to/shekel/scripts/backup_retention.sh >> /var/log/shekel_backup.log 2>&1
        - Weekly verification (Sunday 3:00 AM):
          0 3 * * 0 /path/to/shekel/scripts/verify_backup.sh $(ls -t /var/backups/shekel/shekel_backup_*.sql.gz* | head -1) >> /var/log/shekel_backup.log 2>&1
        - Weekly integrity check (Sunday 3:30 AM):
          30 3 * * 0 docker exec shekel-app python scripts/integrity_check.py >> /var/log/shekel_backup.log 2>&1

    ### 3.2 NAS Mount Configuration
        #### NFS Option
            - Install nfs-utils: pacman -S nfs-utils
            - Create mount point: mkdir -p /mnt/nas/backups/shekel
            - Add fstab entry (documented with example)
            - Mount and verify: mount -a && touch /mnt/nas/backups/shekel/.test && rm /mnt/nas/backups/shekel/.test
        #### CIFS/SMB Option
            - Install cifs-utils: pacman -S cifs-utils
            - Create credentials file: /root/.nas-credentials
            - Add fstab entry (documented with example)
            - Mount and verify

    ### 3.3 Encryption Setup (Optional)
        - Generate a strong passphrase
        - Set BACKUP_ENCRYPTION_PASSPHRASE in environment
        - Store passphrase separately from backups (e.g., password manager)
        - WARNING: lost passphrase = unrecoverable backups

## 4. Manual Backup
    - Command: ./scripts/backup.sh [--local-dir DIR] [--nas-dir DIR] [--no-nas]
    - Verify output file exists and is non-empty

## 5. Retention Policy
    - Policy summary table (daily/weekly/monthly tiers)
    - Command: ./scripts/backup_retention.sh [--dry-run]
    - How to adjust retention periods via environment variables

## 6. Restore Procedure
    ### 6.1 Identify the Backup to Restore
        - List available backups: ls -lht /var/backups/shekel/
        - For NAS backups: ls -lht /mnt/nas/backups/shekel/
        - To restore to a specific date: find the backup with the matching YYYYMMDD

    ### 6.2 Run the Restore
        - Command: ./scripts/restore.sh /path/to/backup.sql.gz
        - The script will prompt for confirmation (type "y" to proceed)
        - For encrypted backups: set BACKUP_ENCRYPTION_PASSPHRASE first

    ### 6.3 Verify the Restore
        - Check the application at http://localhost:5000
        - Verify data: login and check the budget grid
        - Run integrity checks: docker exec shekel-app python scripts/integrity_check.py

    ### 6.4 Restoring from NAS
        - If local backups are unavailable, copy from NAS first:
          cp /mnt/nas/backups/shekel/shekel_backup_YYYYMMDD_HHMMSS.sql.gz /tmp/
          ./scripts/restore.sh /tmp/shekel_backup_YYYYMMDD_HHMMSS.sql.gz

## 7. Backup Verification
    - Command: ./scripts/verify_backup.sh /path/to/backup.sql.gz
    - What it checks (sanity queries + integrity checks)
    - Expected output (PASS / PASS WITH WARNINGS / FAIL)
    - Recommended schedule: weekly (Sunday 3:00 AM)

## 8. Integrity Checks
    - Command: docker exec shekel-app python scripts/integrity_check.py [--verbose]
    - Check categories and severity levels
    - How to interpret results
    - What to do when a check fails

## 9. Troubleshooting
    ### Common Issues
        - "Database container is not running" → docker start shekel-db
        - "NAS directory does not exist" → verify mount: mount | grep nas
        - "Backup file is empty" → check disk space, PostgreSQL logs
        - "Encrypted backup but no passphrase" → set BACKUP_ENCRYPTION_PASSPHRASE
        - "Restore failed: permission denied" → verify PGUSER has CREATEDB privilege
        - "Integrity check CRITICAL failures after restore" → may indicate corrupt backup; try an older backup

    ### Log Files
        - Backup script logs: /var/log/shekel_backup.log (if cron redirects stdout)
        - Application structured logs: docker logs shekel-app
        - PostgreSQL logs: docker logs shekel-db

## 10. Environment Variables Reference
    | Variable | Default | Used By | Description |
    |----------|---------|---------|-------------|
    | BACKUP_LOCAL_DIR | /var/backups/shekel | backup.sh, retention.sh | Local backup storage |
    | BACKUP_NAS_DIR | /mnt/nas/backups/shekel | backup.sh, retention.sh | NAS backup storage |
    | BACKUP_ENCRYPTION_PASSPHRASE | (none) | backup.sh, restore.sh, verify.sh | GPG encryption passphrase |
    | DB_CONTAINER | shekel-db | all scripts | PostgreSQL Docker container |
    | APP_CONTAINER | shekel-app | restore.sh, verify.sh | App Docker container |
    | PGUSER | shekel_user | all scripts | PostgreSQL user |
    | PGDATABASE | shekel | all scripts | PostgreSQL database name |
    | RETENTION_DAILY_DAYS | 7 | retention.sh | Days to keep daily backups |
    | RETENTION_WEEKLY_WEEKS | 4 | retention.sh | Weeks to keep Sunday backups |
    | RETENTION_MONTHLY_MONTHS | 6 | retention.sh | Months to keep 1st-of-month backups |
```

#### Files to Modify

**`.env.example`** -- Add backup-related environment variables.

Current (lines 39-42):
```
# ── Gunicorn (Docker production only) ────────────────────────────
GUNICORN_WORKERS=2
APP_PORT=5000
```

Add after line 42:

```
# ── Backups ──────────────────────────────────────────────────────
# Local directory for backup files (host path, not inside container).
BACKUP_LOCAL_DIR=/var/backups/shekel
# NAS mount point for off-site backup copies.
BACKUP_NAS_DIR=/mnt/nas/backups/shekel
# GPG passphrase for encrypting backups at rest (optional).
# If empty, backups are stored unencrypted.
# WARNING: Store this passphrase separately from the backups themselves.
BACKUP_ENCRYPTION_PASSPHRASE=
# Retention periods for backup pruning.
RETENTION_DAILY_DAYS=7
RETENTION_WEEKLY_WEEKS=4
RETENTION_MONTHLY_MONTHS=6
```

#### Test Gate

- [ ] `scripts/verify_backup.sh --help` prints usage and exits 0
- [ ] Verification creates a temporary database, runs checks, and drops the database
- [ ] Temporary database is cleaned up even if a check fails (trap handler)
- [ ] Sanity checks detect empty tables (user count, period count, account count)
- [ ] Integrity checks run against the temporary database, not production
- [ ] `docs/backup_runbook.md` covers all procedures (backup, restore, retention, verify, integrity)
- [ ] `.env.example` includes all backup-related environment variables

#### Manual Verification

1. **Happy path:**
   ```bash
   # Create a backup.
   mkdir -p /tmp/shekel_test_backup
   ./scripts/backup.sh --no-nas --local-dir /tmp/shekel_test_backup
   BACKUP_FILE=$(ls -t /tmp/shekel_test_backup/shekel_backup_*.sql.gz | head -1)

   # Verify the backup.
   ./scripts/verify_backup.sh "${BACKUP_FILE}"
   # Expected: all sanity checks PASS, integrity checks PASS, final status PASS.

   # Verify the temp database was cleaned up.
   docker exec shekel-db psql -U shekel_user -d postgres -c "\l" | grep shekel_verify
   # Expected: no output (database was dropped).
   ```

2. **Corrupt backup file:**
   ```bash
   echo "corrupt data" | gzip > /tmp/corrupt_backup.sql.gz
   ./scripts/verify_backup.sh /tmp/corrupt_backup.sql.gz
   # Expected: restore fails, status FAIL, temp database still cleaned up.
   ```

3. **Trap cleanup on failure:**
   ```bash
   # Manually create the verify database to simulate a previous failed run.
   docker exec shekel-db psql -U shekel_user -d postgres -c "CREATE DATABASE shekel_verify OWNER shekel_user;"
   # Run verify -- it should drop and recreate.
   ./scripts/verify_backup.sh "${BACKUP_FILE}"
   # Verify cleanup.
   docker exec shekel-db psql -U shekel_user -d postgres -c "\l" | grep shekel_verify
   ```

---

## Complete Test Plan

### Existing Tests (no changes required)

All existing tests continue to pass without modification across all 5 work units. Phase 8C does not modify any application code, models, routes, or templates. The new files are additive scripts and documentation.

### pytest Tests for `integrity_check.py` (WU-4)

| Test File | Class | Function | WU |
|-----------|-------|----------|----|
| `tests/test_scripts/test_integrity_check.py` | `TestCheckResult` | `test_passing_check` | 4 |
| | | `test_failing_check` | 4 |
| | `TestReferentialIntegrity` | `test_clean_database_passes_all` | 4 |
| | | `test_fk01_detects_orphaned_account` | 4 |
| | | `test_fk05_detects_transaction_with_missing_period` | 4 |
| | | `test_fk10_detects_template_with_missing_category` | 4 |
| | `TestOrphanDetection` | `test_clean_database_no_orphans` | 4 |
| | | `test_or02_detects_unused_recurrence_rule` | 4 |
| | | `test_or03_detects_unused_category` | 4 |
| | | `test_or06_detects_goal_on_inactive_account` | 4 |
| | `TestBalanceAnomalies` | `test_clean_database_no_anomalies` | 4 |
| | | `test_ba01_detects_balance_without_period` | 4 |
| | | `test_ba03_detects_period_gap` | 4 |
| | | `test_ba04_detects_date_overlap` | 4 |
| | `TestDataConsistency` | `test_clean_database_passes` | 4 |
| | | `test_dc01_detects_done_without_actual` | 4 |
| | | `test_dc05_detects_active_template_inactive_account` | 4 |
| | | `test_dc07_detects_user_without_settings` | 4 |
| | | `test_dc08_detects_user_without_baseline` | 4 |
| | | `test_dc09_detects_cross_user_deduction_target` | 4 |
| | `TestRunAllChecks` | `test_runs_all_categories_by_default` | 4 |
| | | `test_category_filter` | 4 |
| | | `test_returns_check_result_objects` | 4 |
| | | `test_exit_code_zero_on_clean_db` | 4 |

**Total new pytest tests: 24**

### Manual Verification Runbook for Shell Scripts (WU-1, WU-2, WU-3, WU-5)

| # | Script | Verification Step | Expected Result |
|---|--------|-------------------|-----------------|
| 1 | `backup.sh` | Run with `--no-nas` and verify `.sql.gz` file is created | Non-empty file in local dir |
| 2 | `backup.sh` | Decompress and inspect SQL content | Valid `pg_dump` output with schema statements |
| 3 | `backup.sh` | Run with invalid NAS path | Exit 0, WARNING log about NAS |
| 4 | `backup.sh` | Run with database container stopped | Exit non-zero, ERROR log |
| 5 | `backup.sh` | Run with `BACKUP_ENCRYPTION_PASSPHRASE` set | `.sql.gz.gpg` file created, `.sql.gz` removed |
| 6 | `backup.sh` | Decrypt and verify encrypted backup | Valid SQL content after GPG + gunzip |
| 7 | `retention.sh` | Create test files with various dates, run `--dry-run` | Correct files flagged for deletion |
| 8 | `retention.sh` | Run actual retention on test files | Old daily files deleted; Sunday/1st-of-month files kept |
| 9 | `retention.sh` | Verify Sunday detection from filename | Only files with Sunday dates classified as weekly |
| 10 | `retention.sh` | Verify 1st-of-month detection from filename | Only `*_01_*.sql.gz` files classified as monthly |
| 11 | `retention.sh` | Run with missing NAS directory | WARNING log, continues without error |
| 12 | `restore.sh` | Press Enter at confirmation prompt | "Restore cancelled" message, no changes |
| 13 | `restore.sh` | Type "y" at prompt, restore from known backup | Database restored, app accessible |
| 14 | `restore.sh` | Verify database has expected data after restore | User count, period count, transaction count match |
| 15 | `restore.sh` | Restore encrypted backup with passphrase | Same result as unencrypted restore |
| 16 | `restore.sh` | Attempt to restore nonexistent file | Exit non-zero, ERROR log |
| 17 | `restore.sh` | Restore from older backup (before latest migration) | Migrations applied, app starts normally |
| 18 | `verify.sh` | Run against a known-good backup | All sanity checks PASS, final status PASS |
| 19 | `verify.sh` | Verify temporary database is dropped after run | `\l` shows no `shekel_verify` database |
| 20 | `verify.sh` | Run against a corrupt backup file | Restore fails, final status FAIL, temp DB cleaned up |
| 21 | `verify.sh` | Kill the script mid-execution (Ctrl+C) | Trap handler drops temp database |
| 22 | `verify.sh` | Run against encrypted backup | Decryption works, checks pass |

---

## Phase 8C Test Gate Checklist (Expanded)

From the Phase 8 plan, with specific test/verification references:

- [ ] `backup.sh` produces a valid compressed dump file in local destination: Manual verification #1, #2
- [ ] `backup.sh` produces a valid compressed dump file in NAS destination: Manual verification #1 (with actual NAS mount)
- [ ] `backup_retention.sh` correctly prunes files according to the tiered policy: Manual verification #7, #8, #9, #10
- [ ] `restore.sh` successfully restores a backup to a clean database: Manual verification #13, #14
- [ ] Application starts and functions correctly after a restore: Manual verification #13 (curl to login page), #17 (migration case)
- [ ] `verify_backup.sh` passes all sanity checks on a known-good backup: Manual verification #18
- [ ] Backup failure (e.g., NAS unreachable) produces a non-zero exit code and logs an error: Manual verification #3 (NAS warning; local succeeds), #4 (DB container stopped; full failure)
- [ ] `integrity_check.py` detects referential integrity violations: `TestReferentialIntegrity.test_fk01_detects_orphaned_account`
- [ ] `integrity_check.py` detects orphaned records: `TestOrphanDetection.test_or02_detects_unused_recurrence_rule`
- [ ] `integrity_check.py` detects balance anomalies: `TestBalanceAnomalies.test_ba03_detects_period_gap`
- [ ] `integrity_check.py` detects data consistency issues: `TestDataConsistency.test_dc07_detects_user_without_settings`
- [ ] `integrity_check.py` passes on a clean, properly seeded database: `TestRunAllChecks.test_exit_code_zero_on_clean_db`
- [ ] All 24 new pytest tests pass: `pytest tests/test_scripts/test_integrity_check.py -v`
- [ ] All existing tests continue to pass: `pytest`
- [ ] Runbook documentation is complete: `docs/backup_runbook.md` covers all procedures

---

## File Summary

### New Files (7)

| File | Type | WU |
|------|------|----|
| `scripts/backup.sh` | Shell script | 1 |
| `scripts/backup_retention.sh` | Shell script | 2 |
| `scripts/restore.sh` | Shell script | 3 |
| `scripts/integrity_check.py` | Python script | 4 |
| `scripts/verify_backup.sh` | Shell script | 5 |
| `docs/backup_runbook.md` | Documentation | 5 |
| `tests/test_scripts/test_integrity_check.py` | Python test | 4 |

### Modified Files (1)

| File | Changes | WU |
|------|---------|-----|
| `.env.example` | Add backup-related environment variables (`BACKUP_LOCAL_DIR`, `BACKUP_NAS_DIR`, `BACKUP_ENCRYPTION_PASSPHRASE`, retention periods) | 5 |
