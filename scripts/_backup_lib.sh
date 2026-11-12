#!/bin/bash
# Shekel Budget App -- Shared helpers for the backup family.
# SOURCE this file (". _backup_lib.sh"); do not execute it.
#
# Single home for the concepts backup.sh, restore.sh, verify_backup.sh,
# and backup_retention.sh previously each hand-maintained (polyglot audit
# 2026-06-12, finding OPS/SH-06 in docs/audits/polyglot-cleanup/findings.md):
# the application schema set (three divergent copies meant a new schema
# missed in backup.sh would silently drop that schema from every backup,
# discovered only at restore time), the log() function, the docker
# prerequisite stanza, and the decrypt/decompress pipeline.  Since card
# plan#2166 also the compose project a container was made from, which
# restore.sh starts the app with and verify_backup.sh runs the integrity
# check in.
#
# errexit discipline: every helper uses explicit `if ! cmd; return 1`
# control flow instead of relying on set -e, because callers routinely
# invoke helpers in `cmd || rc=$?` contexts where bash DISABLES errexit
# for the entire function body -- the exact mechanism behind audit
# findings OPS/SH-03 and OPS/SH-07.

# ── Schema set (single source of truth) ──────────────────────────
# public  -- alembic_version (migration state); always exists, never CREATEd
# ref     -- lookup/reference tables
# auth    -- users, sessions, MFA
# budget  -- pay periods, transactions, accounts, templates
# salary  -- salary profiles, deductions, tax configs
# system  -- audit_log
SHEKEL_DUMP_SCHEMAS=(public ref auth budget salary system)
SHEKEL_APP_SCHEMAS=(ref auth budget salary system)

# Echo pg_dump --schema flags for every application schema.
shekel_pg_dump_schema_flags() {
    local schema
    for schema in "${SHEKEL_DUMP_SCHEMAS[@]}"; do
        printf -- '--schema=%s ' "${schema}"
    done
}

# Echo the CREATE SCHEMA IF NOT EXISTS statements for the app schemas
# (public is owned by postgres and always present).
shekel_create_schema_sql() {
    local schema
    for schema in "${SHEKEL_APP_SCHEMAS[@]}"; do
        printf 'CREATE SCHEMA IF NOT EXISTS %s; ' "${schema}"
    done
}

# ── Logging ──────────────────────────────────────────────────────
log() {
    # Structured log output: [YYYY-MM-DD HH:MM:SS] [LEVEL] message
    local level="$1"
    shift
    # shellcheck disable=SC2312 # date with a literal format string always succeeds; the timestamp is display-only inside this log line, never fed to a downstream operation
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [${level}] $*"
}

# ── Prerequisites ────────────────────────────────────────────────
# "Is this container running?" has one home in the shell scripts, shared
# with deploy/shekel-deploy.sh and scripts/restore.sh (plan step
# balance:X-dm, ruling R-BAL254).
# shellcheck source=_container_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/_container_lib.sh"

# Verify docker exists and the named DB container is running.
# Usage: require_db_container "$DB_CONTAINER" || exit 1
require_db_container() {
    local container="$1"
    if ! command -v docker &>/dev/null; then
        log "ERROR" "docker command not found"
        return 1
    fi
    # shellcheck disable=SC2310 # container_running is a boolean predicate (one captured docker inspect); its 0/1 is exactly what this if tests.
    if ! container_running "${container}"; then
        log "ERROR" "Database container '${container}' is not running"
        return 1
    fi
    return 0
}

# ── The compose project a container was made from ────────────────
# Print one command for the operator to paste, each word shell-quoted.
print_command() {
    local quoted
    printf -v quoted '%q ' "$@"
    echo "      ${quoted% }"
}

# compose_project_of KIND CONTAINER: fill COMPOSE_COMMAND with the docker
# compose command, without its subcommand, that made CONTAINER, and
# COMPOSE_SERVICE with its service.  Both are read from the labels docker
# compose stamps on every container it makes: its project, its directory,
# its files and its service.  A command built from them reaches the stack
# the container belongs to and no other: never a setting of the script's
# own that could name another, and never the compose file of whatever
# directory the script runs from (the repository's bare docker-compose.yml
# is project shekel-prod, and an up of it would recreate production's
# containers from the base file alone).  So an empty label is refused
# (docker prints a missing one as empty), as is a project that no longer
# parses.  KIND names the container in a refusal ("App container").
# Usage: compose_project_of "App container" "$APP_CONTAINER" || exit 1
COMPOSE_COMMAND=()
COMPOSE_SERVICE=""
compose_project_of() {
    local kind="$1" container="$2"
    local -a keys=(project project.working_dir project.config_files service)
    local -a values files
    local template="" key file labels index
    for key in "${keys[@]}"; do
        template+="{{println (index .Config.Labels \"com.docker.compose.${key}\")}}"
    done
    if ! labels=$(docker inspect --format "${template}" "${container}"); then
        log "ERROR" "${kind} '${container}': docker cannot read its labels (above)"
        return 1
    fi
    mapfile -t values <<<"${labels}"
    for index in "${!keys[@]}"; do
        if [[ -z "${values[index]:-}" ]]; then
            log "ERROR" "${kind} '${container}' has no com.docker.compose.${keys[index]} label, so this"
            log "ERROR" "script cannot reach its stack the way docker compose made it.  Make the"
            log "ERROR" "container with docker compose in its stack's directory, then run this again."
            return 1
        fi
    done

    COMPOSE_COMMAND=(docker compose --project-name "${values[0]}" --project-directory "${values[1]}")
    IFS=, read -r -a files <<<"${values[2]}"
    for file in "${files[@]}"; do
        COMPOSE_COMMAND+=(--file "${file}")
    done
    # shellcheck disable=SC2034 # an output of this function, read by the scripts that source this library (restore.sh starts the app's service with it)
    COMPOSE_SERVICE="${values[3]}"

    if ! "${COMPOSE_COMMAND[@]}" config --quiet; then
        log "ERROR" "${kind} '${container}': the compose project that made it does not parse"
        log "ERROR" "(its error is above).  The check that failed:"
        print_command "${COMPOSE_COMMAND[@]}" config --quiet
        return 1
    fi
    return 0
}

# ── Backup artifact handling ─────────────────────────────────────
# Refuse encrypted artifacts without a passphrase.
# Usage: require_passphrase_for "$file" "$BACKUP_ENCRYPTION_PASSPHRASE" || exit 1
require_passphrase_for() {
    local file="$1" passphrase="$2"
    if [[ "${file}" == *.gpg && -z "${passphrase}" ]]; then
        log "ERROR" "Backup is encrypted but BACKUP_ENCRYPTION_PASSPHRASE is not set"
        return 1
    fi
    return 0
}

# Stream a backup artifact's decrypted, decompressed SQL to stdout.
# Reads BACKUP_ENCRYPTION_PASSPHRASE from the environment for .gpg files
# (passphrase travels over fd 0 of gpg, never argv).
backup_stream() {
    local file="$1"
    if [[ "${file}" == *.gpg ]]; then
        echo "${BACKUP_ENCRYPTION_PASSPHRASE}" \
            | gpg --batch --passphrase-fd 0 --quiet -d "${file}" \
            | gunzip
    else
        gunzip -c "${file}"
    fi
}

# Fully validate a backup artifact WITHOUT touching any database: the
# passphrase must decrypt it (for .gpg), the gzip stream must pass an
# end-to-end integrity decode, and the decoded SQL must be non-empty.
# This is the pre-flight that must pass BEFORE any destructive restore
# step (audit OPS/SH-01: restore previously dropped the production
# database before reading a single byte of the artifact, so a corrupt
# file or wrong passphrase left an empty database and no way back).
validate_backup_artifact() {
    local file="$1"
    local decoded_bytes

    if [[ ! -s "${file}" ]]; then
        log "ERROR" "Backup artifact is missing or empty: ${file}"
        return 1
    fi
    # Subshell-local pipefail: the gpg/gunzip stage failures must reach the
    # if-condition even when a caller runs this helper without pipefail.
    if ! decoded_bytes=$(
        set -o pipefail
        backup_stream "${file}" | wc -c
    ); then
        log "ERROR" "Backup artifact failed validation (decryption or gzip integrity): ${file}"
        log "ERROR" "Check BACKUP_ENCRYPTION_PASSPHRASE (for .gpg) and the file's integrity."
        return 1
    fi
    if [[ "${decoded_bytes}" -eq 0 ]]; then
        log "ERROR" "Backup artifact decodes to ZERO bytes of SQL: ${file}"
        return 1
    fi
    log "INFO" "Backup artifact validated: ${file} (${decoded_bytes} bytes of SQL)"
    return 0
}
