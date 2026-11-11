#!/bin/bash
# Shekel Budget App -- what the app image's entrypoints share.
# SOURCE this file; do not execute it.
#
# Two entrypoints start a container from the app's image, and each loads its
# own credential and no other (rulings plan#3412, plan#3413):
#
#   entrypoint.sh                 The web server's, the image's ENTRYPOINT:
#                                 the web login, shekel_app.
#   scripts/owner_entrypoint.sh   The migrate service's, and every command
#                                 run there: the database owner, shekel_owner.
#
# scripts/deploy.sh, the migrate service's command, sources it too, for the
# connection settings.  This file is the one home of those settings and their
# defaults, the secret loader, the database URL built from them, and the wait
# for PostgreSQL.

# ── The connection settings ──────────────────────────────────────
# Defaulted once, here, and exported, so the script an entrypoint execs reads
# the values the entrypoint built its URL from.
export DB_HOST="${DB_HOST:-db}"
export DB_PORT="${DB_PORT:-5432}"
export DB_NAME="${DB_NAME:-shekel}"

# ── Docker secrets (audit finding F-148 / C-38) ──────────────────
# When the operator opts into Docker secrets, the secret values ride into the
# container as files under /run/secrets/<name> instead of as environment
# variables baked into the container's Container.Config.Env.  Loading them
# into the environment here satisfies everything downstream (Flask's
# configuration, psql, the database URL an entrypoint builds), which all
# read environment variables.
#
# Trade-off documented in the audit plan: the real values briefly live in env
# at runtime, but they no longer live at rest in .env or in ``docker
# inspect`` output.  The placeholder values an operator leaves in .env to
# satisfy the base file's ``${VAR:?...}`` interpolation are harmless once the
# entrypoint overwrites them.
#
# Each secret is opt-in: a missing file is a silent no-op (operator is using
# the env-backed posture).  An empty file is rejected -- the operator's intent
# to use file-backed secrets is clear (the file exists) but the file has no
# value to load, which would otherwise surface as a confusing auth error far
# downstream.  See ``docs/runbook_secrets.md`` "Migrating to Docker secrets"
# for the end-to-end migration procedure.
_load_secret() {
    local var_name="$1"
    local file_name="$2"
    local secret_path="/run/secrets/${file_name}"
    local secret_value

    if [ ! -f "${secret_path}" ]; then
        return 0
    fi

    if [ ! -r "${secret_path}" ]; then
        echo "ERROR: secret file ${secret_path} exists but is not readable." >&2
        echo "       Check permissions on the host bind-mount source" >&2
        echo "       (typically /opt/docker/shekel/secrets/${file_name})." >&2
        exit 1
    fi

    # ``$(< file)`` reads via shell redirection (no fork) and bash
    # command substitution strips the trailing newline a typical
    # ``echo "value" > file`` leaves behind, so the in-process value
    # matches what ``printf 'value' > file`` would produce.  Internal
    # newlines (the rare PEM-encoded secret) are preserved.
    secret_value="$(<"${secret_path}")"

    if [ -z "${secret_value}" ]; then
        echo "ERROR: secret file ${secret_path} is present but empty." >&2
        echo "       Either populate the file with the secret value, or" >&2
        echo "       remove the file to fall back to the corresponding" >&2
        echo "       env var." >&2
        exit 1
    fi

    export "${var_name}=${secret_value}"
    # Log the var name (public knowledge) but never the value.
    echo "Loaded ${var_name} from ${secret_path}."
}

# ── The database URL ─────────────────────────────────────────────
# _database_url ROLE PASSWORD: print the libpq URL that logs in as ROLE with
# PASSWORD to the configured database.
#
# DB_SSLMODE is set on the shared-mode production override only
# (deploy/docker-compose.prod.yml; audit finding F-154 / Commit C-37), and its
# value is appended verbatim as the ``sslmode`` query parameter (libpq takes
# disable, allow, prefer, require, verify-ca and verify-full).  Bundled mode
# leaves it unset and keeps the plaintext form, so the README's Quick Start
# works without operator-generated certificates.  ``printf`` is a builtin, so
# the password reaches no process's argument list.
_database_url() {
    local role="$1" password="$2" sslmode_query=""
    if [ -n "${DB_SSLMODE:-}" ]; then
        sslmode_query="?sslmode=${DB_SSLMODE}"
    fi
    printf 'postgresql://%s:%s@%s:%s/%s%s' "${role}" "${password}" \
        "${DB_HOST}" "${DB_PORT}" "${DB_NAME}" "${sslmode_query}"
}

# ── The wait for PostgreSQL ──────────────────────────────────────
# _wait_for_postgres ROLE: return once the database server accepts
# connections, or exit 1 after a minute of asking.  pg_isready asks without
# logging in, so it needs no password; ROLE is the login its request names.
_wait_for_postgres() {
    local role="$1" tries=0 max_tries=60
    echo "Waiting for PostgreSQL..."
    until pg_isready -h "${DB_HOST}" -p "${DB_PORT}" -U "${role}" -q; do
        tries=$((tries + 1))
        if [ "${tries}" -ge "${max_tries}" ]; then
            echo "ERROR: PostgreSQL not ready after ${max_tries} seconds." >&2
            echo "Verify DB_HOST and DB_PORT are correct and the database container is running." >&2
            exit 1
        fi
        sleep 1
    done
    echo "PostgreSQL is ready."
}
