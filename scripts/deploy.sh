#!/bin/bash
# The deploy: the migrate service's command, run by scripts/owner_entrypoint.sh
# as the database owner, shekel_owner, once per deploy and before the web
# server starts (rulings plan#3412, plan#3413).
#
#   1. The schemas                 scripts/init_db.sql
#   2. The web login's grants      scripts/init_db_role.sql
#   3. The fresh build or the migrations, the reference rows, the ledger
#      hooks and the audit-trigger check, in one transaction
#                                  scripts/init_database.py
#   4. The first user's seed, once scripts/seed_user.py
#
# It exits 0 only when every step passed, and the web server's container
# starts only then.  The logins themselves, and every password, are the
# database container's (deploy/postgres/provision_roles.sh): the owner can
# grant on the objects it owns, and it makes no login.
set -eEuo pipefail

# ── Error handler ────────────────────────────────────────────────
# Prints troubleshooting guidance when any command fails.
# Fired by the ERR trap before set -e terminates the script.
deploy_failed() {
    local exit_code=$?
    echo ""
    echo "========================================"
    echo "  Shekel deploy failed (exit code: ${exit_code})"
    echo "========================================"
    echo ""
    echo "Check the output above for the specific error."
    echo ""
    echo "Common causes:"
    echo "  - shekel_owner cannot log in: the database has no such login yet,"
    echo "    or its password is not the one this container holds.  Run"
    echo "    deploy/postgres/provision_roles.sh in the database container."
    echo "  - Database migration conflict"
    echo "  - Audit triggers absent or short of the expected count"
    echo "  - SEED_USER_* missing or malformed for the first user's seed"
    echo ""
    echo "To view full logs:  docker compose logs migrate"
    echo "Troubleshooting:    https://github.com/saltyreformed-labs/Shekel#troubleshooting"
    echo ""
}
trap deploy_failed ERR

# The connection settings, shared with the entrypoints.
# shellcheck source=_entrypoint_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/_entrypoint_lib.sh"
_scripts="$(dirname "${BASH_SOURCE[0]}")"

# The owner's entrypoint is what makes this the owner's deploy: it loads the
# owner's password and sets the deploy's configuration.  Run any other way,
# the steps below would connect as whatever the environment names.
if [ -z "${OWNER_ROLE_PASSWORD:-}" ] || [ "${FLASK_ENV:-}" != deploy ]; then
    echo "ERROR: scripts/deploy.sh runs under scripts/owner_entrypoint.sh, the" >&2
    echo "       migrate service's entrypoint, which loads the owner's password" >&2
    echo "       and sets FLASK_ENV=deploy." >&2
    exit 1
fi

echo "=== Shekel Deploy ==="

# _owner_psql ARGS...: psql as the owner.  The password reaches psql through
# its own environment, never its argument list, which any process in the
# container could read in /proc/<pid>/cmdline (polyglot audit 2026-06-12,
# OPS/SH-09).  Where the compose file sets PGSSLMODE, psql takes it as the
# URL takes DB_SSLMODE.
_owner_psql() {
    PGPASSWORD="${OWNER_ROLE_PASSWORD}" psql \
        -h "${DB_HOST}" -p "${DB_PORT}" -U shekel_owner -d "${DB_NAME}" \
        -v ON_ERROR_STOP=1 -q "$@"
}

# ── 1. Create schemas (init_db.sql) ──────────────────────────────
# Idempotent, so safe to run on every deploy.  The owner creates them, so
# the owner owns them.
echo "Creating database schemas..."
_owner_psql -f "${_scripts}/init_db.sql"
echo "Schemas ready."

# ── 2. Grant shekel_app its privileges (init_db_role.sql) ────────
# shekel_app, the web login, is made in the database container; here the
# owner grants it data reads and writes on what the owner owns, and the
# default privileges for what the migrations below create.
echo "Granting shekel_app its privileges..."
_owner_psql -f "${_scripts}/init_db_role.sql"
echo "Grants ready."

# ── 3. Initialize database (fresh) or run migrations (existing) ─
# The deploy's configuration (FLASK_ENV=deploy, app/config.py's
# DeployConfig; ruling plan#3451), which scripts/owner_entrypoint.sh sets
# for everything run in this container.  It connects by DATABASE_URL, the
# owner, alone, and asks for no web secret.
#
# ONE transaction, committed once (plan step balance:X-cv): the fresh
# build or the migrations, the reference-data seed, the three deploy
# hooks and the audit-trigger check commit together or not at all
# (rulings R-BAL105, R-BAL122).  (A tax-law copy into every user's rows
# ran here until plan step salary:X-at-1 gave the law one home in the
# code, app/tax_law/, which no deploy copies.)
# A failure here leaves alembic_version unmoved, so
# deploy/shekel-deploy.sh can re-pin the previous image; a failure in a
# LATER step lands after this commit.
echo "Initializing database..."
python "${_scripts}/init_database.py"

# ── 4. Seed initial user (optional, first run only) ────────────
# Only runs if SEED_USER_EMAIL is set and non-empty AND the seed
# sentinel file is absent.  seed_user.py is itself idempotent at the
# database level (skips when the user row exists), so the sentinel
# is purely a noise-reduction measure: it spares the operator a
# "User already exists" log line on every deploy.
#
# Seed sentinel path:
#   The sentinel lives in a writable named volume mounted at
#   /home/shekel/app/state.  A writable mount is required because
#   the production rootfs is ``read_only: true`` (Commit C-35).  The
#   volume persists across containers, so the sentinel survives every
#   later deploy; recreating the volume (e.g. operator-driven cleanup)
#   re-runs the seed script's idempotent path on the next deploy.
#
# Alternative to seed-script: leave SEED_USER_EMAIL empty and use
# /register instead.  seed_user.py delegates to
# registration_service.register_user() -- the same provisioning path the
# /register web route uses -- so both create the identical shape in
# one transaction: user, settings, the pay-period schedule, checking
# account, baseline scenario and default categories.  Neither writes tax
# data: the tax law is one copy in the code (plan step salary:X-at-1).
#
# SEED_USER_LAST_PAYDAY is REQUIRED when this step runs (plan step
# X-ad-a): registration no longer invents a pay period, so the seeded
# owner's real payday has to come from somewhere and there is no
# honest default for it.  seed_user.py exits 1 with an actionable
# message when it is unset, malformed, or outside one cadence of
# today -- and `set -e` aborts the deploy, so a misconfigured
# first boot fails loudly instead of provisioning a wrong calendar.
#
# The seed's credentials are read here alone: the web server's container
# is never given them (audit finding F-022), and seed_user.py scrubs them
# from its own environment.
SEED_STATE_DIR="/home/shekel/app/state"
SEED_SENTINEL="${SEED_STATE_DIR}/.seed-complete"
if [ -n "${SEED_USER_EMAIL:-}" ]; then
    if [ -f "${SEED_SENTINEL}" ]; then
        echo "Seed sentinel present at ${SEED_SENTINEL}; skipping user seed step."
    else
        echo "Seeding initial user..."
        python "${_scripts}/seed_user.py"
        echo "User seeding complete."
        # Materialise the sentinel only after the seed script
        # returned cleanly -- ``set -e`` above would have aborted the
        # entire deploy on non-zero, but the explicit ordering
        # also documents the contract: the sentinel records SUCCESS,
        # never the mere fact that we attempted to seed.  ``mkdir -p``
        # tolerates the directory already existing (initial volume
        # bring-up on a fresh deploy) without failing under set -e.
        mkdir -p "${SEED_STATE_DIR}"
        : >"${SEED_SENTINEL}"
    fi
else
    echo "SEED_USER_EMAIL not set, skipping user seed. Use /register to create your account."
fi

echo "=== Shekel Deploy complete ==="
