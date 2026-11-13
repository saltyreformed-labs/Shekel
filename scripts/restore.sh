#!/bin/bash
# Shekel Budget App -- Database Restore
#
# Restores the Shekel database from a backup file produced by backup.sh.
#
# Restore sequence:
#     1. VALIDATE the backup artifact end-to-end (decrypt + gzip integrity +
#        non-empty) -- nothing destructive happens until the artifact proves
#        restorable (polyglot audit 2026-06-12, OPS/SH-01: the previous
#        sequence dropped the production database before reading a single
#        byte, so a wrong passphrase or truncated file left an empty DB with
#        no recovery path)
#     2. Take a pre-restore SAFETY DUMP of the current database
#     3. Stop the application container (if running)
#     4. Terminate existing database connections
#     5. Drop and recreate the database with schemas
#     6. Restore from the backup file (via psql --single-transaction)
#     7. Hand every restored object to the owner login, shekel_owner, with
#        the database container's provisioning script: the dump is taken
#        --no-owner --no-privileges (backup.sh), so the restore makes each
#        object the superuser's, which the owner's deploy cannot change
#     8. Start the app the way docker compose made it, read from the app
#        container's own labels: the start runs the deploy in the migrate
#        service first (the migrations, the web login's grants), while the
#        app is still stopped (ruling plan#3534)
#     9. Verify the restore with basic sanity checks
#
# The app's compose project and the provisioning script are both checked
# before anything is changed.  If anything fails once the database is
# dropped, the script prints the state it left and how to recover from it
# or finish: the safety dump while the restore is incomplete, the step to
# run once it is restored.
#
# Usage:
#     ./scripts/restore.sh <backup_file>
#     ./scripts/restore.sh --skip-confirm <backup_file>
#
# Options:
#     --skip-confirm      Skip the interactive confirmation prompt
#     --skip-safety-dump  Proceed without the pre-restore safety dump (for the
#                         disaster case where the current database is already
#                         lost/unreadable and the dump step itself fails)
#     --help              Show this help message
#
# Exit codes:
#     0   Restore completed successfully
#     1   Fatal error (missing file, validation failure, restore failure)

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_backup_lib.sh"

# ── Configuration ────────────────────────────────────────────────

DB_CONTAINER="${DB_CONTAINER:-shekel-prod-db}"
APP_CONTAINER="${APP_CONTAINER:-shekel-prod-app}"
PGUSER="${PGUSER:-shekel_user}"
PGDATABASE="${PGDATABASE:-shekel}"
BACKUP_ENCRYPTION_PASSPHRASE="${BACKUP_ENCRYPTION_PASSPHRASE:-}"
BACKUP_LOCAL_DIR="${BACKUP_LOCAL_DIR:-/var/backups/shekel}"

# The database container's provisioning script (deploy/postgres/
# provision_roles.sh, which the compose files mount as the postgres image's
# first-boot hook): the one place a login is made, and the hand-over of
# every object to the owner.  A path of the stack, not a setting.
PROVISION_ROLES="/docker-entrypoint-initdb.d/10-provision-roles.sh"
# The hand-over: that script, run in the database container.  POSTGRES_DB
# names the database it hands over, the one this script restores.
HAND_OVER=(docker exec --env "POSTGRES_DB=${PGDATABASE}" "${DB_CONTAINER}" "${PROVISION_ROLES}")

# How far the restore has gone, which the exit trap's guidance reads:
# empty until the DROP DATABASE runs, then dropped, restored, handed-over
# and started.
RESTORE_STAGE=""
SAFETY_DUMP_PATH=""

# The docker compose command that made the app container, without its
# subcommand, and the start this script runs with it, both built from the
# container's own labels by read_app_stack.  Empty when there is no app
# container.
APP_COMPOSE=()
APP_START=()

# With no app container there is no compose start, and so no deploy:
# pending migrations are a host run, as the owner (ruling plan#3608).
HOST_MIGRATION_ADVICE="Run 'flask db upgrade' from the checkout, with .env's DATABASE_URL naming the owner login, shekel_owner (ruling plan#3608), to apply any pending migrations"

# ── Functions ────────────────────────────────────────────────────

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS] <backup_file>

Restore the Shekel database from a backup file.

This will:
    1. Validate the backup artifact (decrypt + integrity) BEFORE any
       destructive step
    2. Take a pre-restore safety dump of the current database
    3. Stop the application container (if running)
    4. Drop and recreate the database
    5. Restore from the backup file
    6. Hand every restored object to the owner login, shekel_owner
    7. Start the app the way docker compose made it, which runs the
       deploy (the migrations, the web login's grants) first
    8. Verify the restore

Arguments:
    backup_file     Path to a .sql.gz or .sql.gz.gpg backup file

Options:
    --skip-confirm      Skip the interactive confirmation prompt
    --skip-safety-dump  Proceed without the pre-restore safety dump
    --help              Show this help message

Environment Variables:
    DB_CONTAINER                  Docker container name for PostgreSQL
    APP_CONTAINER                 Docker container name for the app; its
                                  compose labels name the stack it is
                                  started from
    PGUSER                        PostgreSQL user
    PGDATABASE                    PostgreSQL database name
    BACKUP_ENCRYPTION_PASSPHRASE  GPG passphrase (required for .gpg files)
    BACKUP_LOCAL_DIR              Where the safety dump is written
EOF
}

recovery_guidance() {
    # The exit trap.  Once the database has been dropped, a failure leaves a
    # state the operator must be told, with the way back or forward
    # (OPS/SH-01: the script once died under set -e leaving an empty
    # database and no instructions).  An EXIT trap, because bash runs no ERR
    # trap inside a function without errtrace, and every step here runs
    # inside main: the ERR trap this replaced never ran.  With errtrace it
    # would run again for each pipeline subshell a failure passes through,
    # as in restore_backup's (both measured 2026-11-12).  The exit trap runs
    # once, in this shell, and the script still exits with its own status.
    local status=$?
    local headline
    if [[ ${status} -eq 0 ]]; then
        return 0
    fi
    case "${RESTORE_STAGE}" in
        dropped) headline="RESTORE FAILED INSIDE THE DESTRUCTIVE WINDOW" ;;
        restored) headline="RESTORED, BUT NOT HANDED TO THE OWNER" ;;
        handed-over) headline="RESTORED, BUT THE APP DID NOT START" ;;
        *) return 0 ;;
    esac
    {
        echo ""
        echo "============================================================"
        echo "  ${headline}"
        echo "============================================================"
        case "${RESTORE_STAGE}" in
            dropped)
                echo "  The database '${PGDATABASE}' was dropped and may be empty"
                echo "  or partially restored."
                if [[ -n "${SAFETY_DUMP_PATH}" ]]; then
                    echo "  A pre-restore safety dump of the PREVIOUS state exists:"
                    echo "      ${SAFETY_DUMP_PATH}"
                    echo "  Recover it with:"
                    echo "      ./scripts/restore.sh --skip-safety-dump ${SAFETY_DUMP_PATH}"
                else
                    echo "  No safety dump was taken (--skip-safety-dump was set)."
                    echo "  Recover from the most recent nightly backup."
                fi
                echo "  The app container was left STOPPED on purpose."
                ;;
            restored)
                echo "  The backup is restored into '${PGDATABASE}', but every object"
                echo "  in it is still the superuser's and the web login holds no"
                echo "  grant on any: the owner's deploy cannot change them, and the"
                echo "  app cannot read them.  The app was not started.  Finish with"
                echo "  the hand-over, then the start:"
                print_command "${HAND_OVER[@]}"
                if [[ ${#APP_START[@]} -gt 0 ]]; then
                    print_command "${APP_START[@]}"
                else
                    echo "  ${HOST_MIGRATION_ADVICE}."
                fi
                ;;
            handed-over)
                echo "  The backup is restored into '${PGDATABASE}' and handed to the"
                echo "  owner, but the start failed: the deploy it runs first, in the"
                echo "  migrate service, or the app itself.  Read why, then start it"
                echo "  again:"
                print_command "${APP_COMPOSE[@]}" logs
                print_command "${APP_START[@]}"
                ;;
        esac
        echo "============================================================"
    } >&2
}
trap recovery_guidance EXIT

check_prerequisites() {
    # Everything the restore needs after its drop is checked here, before
    # the artifact's validation and the safety dump, so a restore never
    # drops a database it then cannot hand over or bring the app back to.
    # shellcheck disable=SC2310 # require_db_container is a boolean predicate (explicit if-not/return 1, no internal set -e reliance); the trailing || exit 1 is the intended abort on failure
    require_db_container "${DB_CONTAINER}" || exit 1

    # A stack from before plan#2166 has no provisioning script to hand the
    # restored objects over with.  A file, as well as executable: a
    # directory passes -x.
    # shellcheck disable=SC2016 # the single quotes are deliberate: $1 is the container shell's parameter, expanded there
    if ! docker exec "${DB_CONTAINER}" sh -c 'test -f "$1" && test -x "$1"' sh "${PROVISION_ROLES}"; then
        log "ERROR" "Database container '${DB_CONTAINER}' holds no executable ${PROVISION_ROLES}."
        log "ERROR" "Its compose files predate the database owner login (plan#2166), so a restore"
        log "ERROR" "could not hand the restored objects to it.  Restore with the scripts/restore.sh"
        log "ERROR" "of the release those files came from.  Nothing has been changed."
        exit 1
    fi

    read_app_stack
}

app_container_exists() {
    # Check if the app container exists (may or may not be running).
    docker inspect "${APP_CONTAINER}" &>/dev/null
}

read_app_stack() {
    # Fill APP_COMPOSE and APP_START from the app container's own compose
    # labels (compose_project_of, _backup_lib.sh), so the start brings back
    # the stack this script stopped.  A label missing, or a project that no
    # longer parses, would fail the start after the restore: refused now,
    # before anything is changed.
    # shellcheck disable=SC2310 # app_container_exists is a boolean predicate (docker inspect &>/dev/null); the container-absent non-zero status IS its intended return, not an error to abort on
    if ! app_container_exists; then
        return 0
    fi
    # shellcheck disable=SC2310 # compose_project_of returns explicit codes (the backup library's errexit discipline); this if IS its refusal
    if ! compose_project_of "App container" "${APP_CONTAINER}"; then
        log "ERROR" "Nothing has been changed."
        exit 1
    fi
    APP_COMPOSE=("${COMPOSE_COMMAND[@]}")
    # --pull never: the restore brings back the image it stopped, never a
    # newer one a moving tag names (the base file's :latest is
    # pull_policy: always), and needs no registry to do it.
    APP_START=("${APP_COMPOSE[@]}" up --pull never -d "${COMPOSE_SERVICE}")
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
        [yY][eE][sS] | [yY])
            log "INFO" "Restore confirmed by user"
            ;;
        *)
            log "INFO" "Restore cancelled by user"
            exit 0
            ;;
    esac
}

take_safety_dump() {
    # Dump the CURRENT database before destroying it. Temp-file + rename, same
    # partial-file discipline as backup.sh.
    local ts
    ts="$(date +%Y%m%d_%H%M%S)"
    local dump_path="${BACKUP_LOCAL_DIR}/pre_restore_safety_${ts}.sql.gz"
    local tmp_path="${dump_path}.tmp"

    log "INFO" "Taking pre-restore safety dump: ${dump_path}"
    mkdir -p "${BACKUP_LOCAL_DIR}"
    # shellcheck disable=SC2046 # word splitting of the --schema flags is intended
    # shellcheck disable=SC2310,SC2312 # shekel_pg_dump_schema_flags only printfs a static schema array, so it cannot meaningfully fail; its return value carries no error to mask in this if-condition
    if ! docker exec "${DB_CONTAINER}" pg_dump \
        -U "${PGUSER}" -d "${PGDATABASE}" \
        --clean --if-exists --no-owner --no-privileges \
        $(shekel_pg_dump_schema_flags) \
        | gzip >"${tmp_path}"; then
        rm -f "${tmp_path}"
        log "ERROR" "Pre-restore safety dump FAILED. If the current database is"
        log "ERROR" "already lost (the disaster-recovery case), re-run with"
        log "ERROR" "--skip-safety-dump. Refusing to drop the database otherwise."
        exit 1
    fi
    if [[ ! -s "${tmp_path}" ]]; then
        rm -f "${tmp_path}"
        log "ERROR" "Pre-restore safety dump is empty; refusing to proceed."
        exit 1
    fi
    mv "${tmp_path}" "${dump_path}"
    SAFETY_DUMP_PATH="${dump_path}"
    log "INFO" "Safety dump complete: ${dump_path}"
}

stop_app() {
    # Stop the app container if it exists and is running.
    # shellcheck disable=SC2310 # app_container_exists is a boolean predicate (docker inspect &>/dev/null); the container-absent non-zero status IS its intended return, not an error to abort on
    if app_container_exists; then
        log "INFO" "Stopping application container: ${APP_CONTAINER}"
        docker stop "${APP_CONTAINER}" 2>/dev/null || true
        log "INFO" "Application container stopped"
    else
        log "INFO" "Application container '${APP_CONTAINER}' not found (dev mode); skipping stop"
    fi
}

drop_and_recreate_database() {
    log "INFO" "Dropping and recreating database: ${PGDATABASE}"
    RESTORE_STAGE=dropped

    # Terminate existing connections to the target database.
    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d postgres -c \
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${PGDATABASE}' AND pid <> pg_backend_pid();" \
        >/dev/null 2>&1 || true

    # Drop and recreate the database: the superuser's, like every object the
    # restore then makes in it, until hand_over_to_owner gives it all to the
    # owner.
    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d postgres --quiet -c \
        "DROP DATABASE IF EXISTS ${PGDATABASE};"
    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d postgres --quiet -c \
        "CREATE DATABASE ${PGDATABASE} OWNER ${PGUSER};"

    # Recreate schemas (shared SHEKEL_APP_SCHEMAS list -- OPS/SH-06). The
    # pg_dump --clean output includes DROP/CREATE for tables within schemas,
    # but the schemas themselves must exist first for the restore to succeed.
    # shellcheck disable=SC2312 # shekel_create_schema_sql only printfs static CREATE SCHEMA statements, so it cannot meaningfully fail; there is no inner return value worth surfacing here
    docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${PGDATABASE}" --quiet -c \
        "$(shekel_create_schema_sql)"

    log "INFO" "Database recreated with schemas"
}

restore_backup() {
    local backup_file="$1"

    log "INFO" "Restoring from: ${backup_file}"
    # backup_stream handles .gpg decryption (passphrase via fd, never argv)
    # and gzip decompression for both artifact shapes (OPS/SH-06: the
    # pipeline previously existed twice here and once in verify_backup.sh).
    backup_stream "${backup_file}" \
        | docker exec -i "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${PGDATABASE}" \
            --quiet --single-transaction --set ON_ERROR_STOP=1 --output /dev/null

    log "INFO" "Database restore complete"
    RESTORE_STAGE=restored
}

hand_over_to_owner() {
    # The dump is taken --no-owner --no-privileges (backup.sh), so every
    # object the restore made is the superuser's and holds no grant, schema
    # public among them, which the dump drops and makes again.  The database
    # container's provisioning script, the caller its header names for this
    # case, hands each one to the owner, shekel_owner, and gives public back
    # to pg_database_owner, as a fresh database has it; the owner's deploy
    # (the start below) then grants the web login.
    log "INFO" "Handing the restored database to the owner login: ${PROVISION_ROLES}"
    "${HAND_OVER[@]}"
    RESTORE_STAGE=handed-over
    log "INFO" "Restored database handed to the owner login"
}

start_app() {
    # Bring the app back the way docker compose made it (read_app_stack).
    # The up runs the deploy in the migrate service first, the migrations
    # and then the web login's grants on the restored objects, and starts
    # the app only once that exits 0 (ruling plan#3534), so the app never
    # serves a database its code is ahead of.  A plain docker start runs no
    # deploy (plan#3421).
    if [[ ${#APP_START[@]} -eq 0 ]]; then
        log "INFO" "Application container '${APP_CONTAINER}' not found (dev mode)"
        log "INFO" "${HOST_MIGRATION_ADVICE}"
        RESTORE_STAGE=started
        return 0
    fi

    log "INFO" "Starting the application through docker compose (the deploy runs first):"
    print_command "${APP_START[@]}"
    "${APP_START[@]}"
    RESTORE_STAGE=started

    # Wait for the container to be ready.
    local retries=30
    while [[ ${retries} -gt 0 ]]; do
        # Check for a health check first.
        local health
        health=$(docker inspect --format='{{.State.Health.Status}}' "${APP_CONTAINER}" 2>/dev/null || echo "none")
        if [[ "${health}" == "healthy" ]]; then
            log "INFO" "Application container is healthy"
            return 0
        fi

        # If no health check, just check if it's running: the shell scripts'
        # one running-check (plan step balance:X-dm, ruling R-BAL254), which
        # _backup_lib.sh loads.
        # shellcheck disable=SC2310 # container_running is a boolean predicate (one captured docker inspect); its 0/1 is exactly what this if tests.
        if [[ "${health}" == "none" ]] && container_running "${APP_CONTAINER}"; then
            # Give the entrypoint a moment to finish initialization.
            sleep 3
            log "INFO" "Application container is running"
            return 0
        fi

        # Container might still be starting.
        sleep 2
        retries=$((retries - 1))
    done

    log "WARNING" "Application container did not become ready within 60s"
    log "WARNING" "Check logs: docker logs ${APP_CONTAINER}"
}

verify_restore() {
    # Quick sanity checks against the restored database. Each query's failure
    # is surfaced, not masked (OPS/SH-17: 2>/dev/null previously turned a
    # psql failure into a blank interpolated into a 'Verification:' line).
    log "INFO" "Running post-restore verification..."

    local user_count
    if ! user_count=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${PGDATABASE}" -t -c \
        "SELECT COUNT(*) FROM auth.users;" | tr -d ' '); then
        log "ERROR" "Verification query failed: auth.users count"
        return 1
    fi
    if [[ -z "${user_count}" || "${user_count}" -eq 0 ]]; then
        log "WARNING" "No users found in the restored database. Verify the backup file."
    else
        log "INFO" "Verification: ${user_count} user(s) found in restored database"
    fi

    local period_count
    if ! period_count=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${PGDATABASE}" -t -c \
        "SELECT COUNT(*) FROM budget.pay_periods;" | tr -d ' '); then
        log "ERROR" "Verification query failed: budget.pay_periods count"
        return 1
    fi
    log "INFO" "Verification: ${period_count} pay period(s) in restored database"

    local table_count
    if ! table_count=$(docker exec "${DB_CONTAINER}" psql -U "${PGUSER}" -d "${PGDATABASE}" -t -c \
        "SELECT COUNT(*) FROM information_schema.tables
         WHERE table_schema IN ('ref','auth','budget','salary','system');" | tr -d ' '); then
        log "ERROR" "Verification query failed: table count"
        return 1
    fi
    log "INFO" "Verification: ${table_count} table(s) across all schemas"
}

# ── Main ─────────────────────────────────────────────────────────

main() {
    local skip_confirm=false
    local skip_safety_dump=false
    local backup_file=""

    # Parse command-line arguments.
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --skip-confirm)
                skip_confirm=true
                shift
                ;;
            --skip-safety-dump)
                skip_safety_dump=true
                shift
                ;;
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

    BACKUP_FILE="${backup_file}"

    check_prerequisites
    # shellcheck disable=SC2310 # require_passphrase_for is a boolean predicate (explicit return 1, no internal set -e reliance); the trailing || exit 1 is the intended abort on failure
    require_passphrase_for "${backup_file}" "${BACKUP_ENCRYPTION_PASSPHRASE}" || exit 1

    # The OPS/SH-01 gate: prove the artifact decrypts and decompresses
    # end-to-end BEFORE anything destructive runs.
    # shellcheck disable=SC2310 # validate_backup_artifact returns explicit codes (subshell-local set -o pipefail so pipe failures reach the caller without errexit); the trailing || exit 1 IS the OPS/SH-01 abort-before-destructive gate
    validate_backup_artifact "${backup_file}" || exit 1

    # Confirmation prompt (default: No).
    if [[ "${skip_confirm}" == false ]]; then
        confirm_restore
    fi

    if [[ "${skip_safety_dump}" == false ]]; then
        take_safety_dump
    else
        log "WARNING" "Pre-restore safety dump SKIPPED (--skip-safety-dump)"
    fi

    stop_app
    drop_and_recreate_database
    restore_backup "${backup_file}"
    hand_over_to_owner
    start_app
    verify_restore

    log "INFO" "Restore complete."
    if [[ -n "${SAFETY_DUMP_PATH}" ]]; then
        log "INFO" "Pre-restore safety dump retained at: ${SAFETY_DUMP_PATH}"
        log "INFO" "(delete it once the restored state is confirmed good)"
    fi
    # shellcheck disable=SC2310 # app_container_exists is a boolean predicate (docker inspect &>/dev/null); the container-absent non-zero status IS its intended return, not an error to abort on
    if app_container_exists; then
        log "INFO" "Review the application at http://localhost:5000 to verify."
    else
        log "INFO" "Run 'flask run' and verify the application manually."
    fi
}

main "$@"
