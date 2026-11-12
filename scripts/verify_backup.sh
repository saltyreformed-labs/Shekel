#!/bin/bash
# Shekel Budget App -- Backup Verification
#
# Verifies a backup by restoring it to a temporary database, running
# sanity check queries and the Python integrity check script, then
# dropping the temporary database.
#
# The copy is made, restored into and owned by the database owner,
# shekel_owner, over the database container's own socket, which needs no
# password; the integrity check then runs in the stack's migrate service,
# whose entrypoint connects as the owner, pointed at the copy (ruling
# plan#3607).  No password leaves any container.
#
# The production database is never touched.
#
# Usage:
#     ./scripts/verify_backup.sh <backup_file>
#
# Options:
#     --help          Show this help message
#
# Exit codes:
#     0   All checks passed
#     1   One or more checks failed
#     2   Warnings only (no critical failures)
#
# Cron example (weekly, Sunday at 3:00 AM):
#     0 3 * * 0 /path/to/shekel/scripts/verify_backup.sh \
#         $(ls -t /var/backups/shekel/shekel_backup_*.sql.gz* | head -1) \
#         >> /var/log/shekel_backup.log 2>&1

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_backup_lib.sh"

# ── Configuration ────────────────────────────────────────────────

DB_CONTAINER="${DB_CONTAINER:-shekel-prod-db}"
PGUSER="${PGUSER:-shekel_user}"
PGDATABASE="${PGDATABASE:-shekel}"
# Per-run suffix so two overlapping verification runs cannot drop each
# other's temp database mid-restore (OPS/SH-27).
VERIFY_DB="${VERIFY_DB:-shekel_verify_$$}"
BACKUP_ENCRYPTION_PASSPHRASE="${BACKUP_ENCRYPTION_PASSPHRASE:-}"

# The database owner login (deploy/postgres/provision_roles.sh), which
# makes, fills and owns the copy, and the stack's service that connects as
# it (scripts/owner_entrypoint.sh).
OWNER_ROLE="shekel_owner"
MIGRATE_SERVICE="migrate"

# The docker compose command, without its subcommand, that made the
# database container, read from its labels by check_prerequisites.
DB_COMPOSE=()

# ── Functions ────────────────────────────────────────────────────

usage() {
    cat <<EOF
Usage: $(basename "$0") <backup_file>

Verify a Shekel backup by restoring to a temporary database and running
sanity checks.

This will:
    1. Create a temporary database (${VERIFY_DB}), owned by ${OWNER_ROLE}
    2. Restore the backup into it as ${OWNER_ROLE}
    3. Run sanity check queries (row counts, user check, date ranges)
    4. Run the integrity check script in the stack's ${MIGRATE_SERVICE}
       service, as ${OWNER_ROLE}, against the copy
    5. Drop the temporary database

The production database is never touched.

Arguments:
    backup_file     Path to a .sql.gz or .sql.gz.gpg backup file

Options:
    --help          Show this help message

Environment Variables:
    DB_CONTAINER                  Docker container name for PostgreSQL; its
                                  compose labels name the stack whose
                                  ${MIGRATE_SERVICE} service runs the check
    PGUSER                        PostgreSQL superuser, over the database
                                  container's socket: makes and drops the
                                  copy, and reads the sanity checks
    PGDATABASE                    Production database name (for reference)
    VERIFY_DB                     Temporary database name for verification
    BACKUP_ENCRYPTION_PASSPHRASE  GPG passphrase (required for .gpg files)
EOF
}

# shellcheck disable=SC2329 # invoked indirectly as the EXIT trap handler registered by 'trap cleanup EXIT' in main, never called by name
cleanup() {
    # Trap handler: always drop the temporary database on exit.
    log "INFO" "Cleaning up: dropping temporary database ${VERIFY_DB}"
    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d postgres --quiet -c \
        "DROP DATABASE IF EXISTS ${VERIFY_DB};" 2>/dev/null || true
}

check_prerequisites() {
    # shellcheck disable=SC2310 # require_db_container is a boolean predicate; the trailing '|| exit 1' propagates its failure explicitly, so masked errexit inside it is irrelevant
    require_db_container "${DB_CONTAINER}" || exit 1

    # The integrity check runs in the migrate service of the stack the
    # database container belongs to, read from its own labels, so the
    # check and the copy are always in one stack.
    # shellcheck disable=SC2310 # compose_project_of returns explicit codes (the backup library's errexit discipline); the trailing '|| exit 1' is the intended abort on failure
    compose_project_of "Database container" "${DB_CONTAINER}" || exit 1
    DB_COMPOSE=("${COMPOSE_COMMAND[@]}")
}

create_temp_database() {
    log "INFO" "Creating temporary database: ${VERIFY_DB}"

    # Drop if it exists from a previous failed run.
    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d postgres --quiet -c \
        "DROP DATABASE IF EXISTS ${VERIFY_DB};" 2>/dev/null || true

    # The owner's, and so its schemas and every object the restore makes:
    # the owner then reads the whole copy with no grant (ruling plan#3607).
    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d postgres --quiet -c \
        "CREATE DATABASE ${VERIFY_DB} OWNER ${OWNER_ROLE};"

    # Create schemas (shared SHEKEL_APP_SCHEMAS list -- OPS/SH-06), as the
    # owner over the database container's socket, which asks no password
    # (local trust: production's pg_hba.conf and the pinned image's, both
    # read 2026-11-12).
    # shellcheck disable=SC2312 # shekel_create_schema_sql only printf's over a static array and cannot fail; its return value carries no signal worth propagating
    docker exec "${DB_CONTAINER}" psql -U "${OWNER_ROLE}" -d "${VERIFY_DB}" --quiet -c \
        "$(shekel_create_schema_sql)"

    log "INFO" "Temporary database created"
}

restore_to_temp() {
    local backup_file="$1"
    log "INFO" "Restoring backup to temporary database as ${OWNER_ROLE}..."

    # shellcheck disable=SC2310 # require_passphrase_for is a boolean predicate; the trailing '|| return 1' propagates its failure explicitly, so masked errexit inside it is irrelevant
    require_passphrase_for "${backup_file}" "${BACKUP_ENCRYPTION_PASSPHRASE}" || return 1
    backup_stream "${backup_file}" \
        | docker exec -i "${DB_CONTAINER}" psql -U "${OWNER_ROLE}" -d "${VERIFY_DB}" \
            --quiet --single-transaction --set ON_ERROR_STOP=1 --output /dev/null

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

    # Check 2: budget.pay_periods row count and payday range.
    #
    # MIN/MAX of start_date, not of end_date: plan step pay_calendar:C4-c
    # dropped end_date, because a period's last covered day is the day before
    # the next payday and is derived rather than stored.  The paydays ARE the
    # range this check reports.
    local period_info
    period_info=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${VERIFY_DB}" -t -c \
        "SELECT COUNT(*), MIN(start_date), MAX(start_date) FROM budget.pay_periods;" | tr -d ' ')
    local period_count min_date max_date
    period_count=$(echo "${period_info}" | cut -d'|' -f1)
    min_date=$(echo "${period_info}" | cut -d'|' -f2)
    max_date=$(echo "${period_info}" | cut -d'|' -f3)
    if [[ "${period_count}" -gt 0 ]]; then
        log "INFO" "  [PASS] budget.pay_periods: ${period_count} periods (paydays ${min_date} to ${max_date})"
    else
        log "INFO" "  [INFO] budget.pay_periods: 0 periods (may be expected for a fresh database)"
    fi

    # Check 3: budget.transactions row count (informational).
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
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'system' AND table_name = 'audit_log');" | tr -d ' ')
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

    local exit_code=0

    # In the migrate service, as the owner: its entrypoint loads the
    # owner's password inside that container and builds the owner's URL to
    # DB_NAME, here the copy, so no password leaves any container (ruling
    # plan#3607).  --no-deps: the database is already up.  --pull never:
    # the check runs the image the stack runs, never a newer one a moving
    # tag names.  The script's exit code is the check's.
    "${DB_COMPOSE[@]}" run --rm --no-deps --pull never \
        --env "DB_NAME=${VERIFY_DB}" "${MIGRATE_SERVICE}" \
        python scripts/integrity_check.py --verbose || exit_code=$?

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
            --help)
                usage
                exit 0
                ;;
            -*)
                log "ERROR" "Unknown option: $1"
                usage
                exit 1
                ;;
            *)
                backup_file="$1"
                shift
                ;;
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

    # Validate encryption requirements before starting.
    if [[ "${backup_file}" == *.gpg && -z "${BACKUP_ENCRYPTION_PASSPHRASE}" ]]; then
        log "ERROR" "Backup is encrypted but BACKUP_ENCRYPTION_PASSPHRASE is not set"
        exit 1
    fi

    check_prerequisites

    # Register cleanup trap -- always drop the temp database on exit.
    trap cleanup EXIT

    create_temp_database
    restore_to_temp "${backup_file}"

    local sanity_failures=0
    local integrity_code=0

    # shellcheck disable=SC2310 # run_sanity_checks deliberately returns its failure COUNT; '|| sanity_failures=$?' captures that count for the summary and exit-code decision below, which is the function's contract, not a swallowed fatal error
    run_sanity_checks || sanity_failures=$?
    # shellcheck disable=SC2310 # run_integrity_checks deliberately returns integrity_check.py's 0/1/2 code; '|| integrity_code=$?' captures it for the summary and is mapped to the script's exit code below, which is the function's contract, not a swallowed fatal error
    run_integrity_checks || integrity_code=$?

    # Report final status.
    echo ""
    log "INFO" "============================================================"
    log "INFO" "  BACKUP VERIFICATION SUMMARY"
    log "INFO" "============================================================"
    log "INFO" "  Backup file:      ${backup_file}"
    log "INFO" "  Sanity checks:    ${sanity_failures} failure(s)"
    log "INFO" "  Integrity checks: exit code ${integrity_code}"

    if [[ ${sanity_failures} -eq 0 && ${integrity_code} -eq 0 ]]; then
        log "INFO" "  Status: PASS"
        log "INFO" "============================================================"
        # cleanup runs via trap
        exit 0
    elif [[ ${integrity_code} -eq 2 && ${sanity_failures} -eq 0 ]]; then
        # -eq, not -le: integrity_check.py's contract is 0=pass, 1=CRITICAL
        # failures, 2=warnings only. The previous -le 2 mapped CRITICAL
        # failures to PASS WITH WARNINGS / exit 2, so cron monitoring keyed
        # on exit 1 never fired for a critically-broken backup (OPS/SH-02).
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
