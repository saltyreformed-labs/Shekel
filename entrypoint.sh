#!/bin/bash
# The web server's entrypoint: the image's ENTRYPOINT, run before gunicorn
# (or the dev compose's ``flask run``), which it execs.
#
# It holds the web server's credentials and no other (rulings plan#3412,
# plan#3413): the secret key, the web login's password, the TOTP keys and the
# rate-limit store's password.  It does no database work but wait for the
# server.  The deploy -- the schemas, the grants, the migrations, the
# reference rows and the first user's seed -- runs before this container
# starts, in the one-shot migrate service, as the database owner
# (scripts/owner_entrypoint.sh, scripts/deploy.sh).  So this script loads no
# owner or superuser credential, and gunicorn, which inherits its
# environment, holds none.
#
# -u (nounset): a typo'd variable dies loudly instead of expanding empty
# (polyglot audit 2026-06-12, OPS/SH-23 -- every other script in the repo
# already runs with it). Optional variables are referenced with :- below.
set -euo pipefail

# The secret loader, the wait for PostgreSQL and the database URL, shared
# with the migrate service's entrypoint.
# shellcheck source=scripts/_entrypoint_lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/scripts/_entrypoint_lib.sh"

echo "=== Shekel Entrypoint ==="

# ── 0. Load Docker secrets if present (audit finding F-148 / C-38) ─
# The web server's five, each from /run/secrets/<name> when the file is
# mounted, else its environment variable (scripts/_entrypoint_lib.sh).  The
# rate-limit store's password is read from the file the valkey service's
# deploy/valkey/start.sh reads first too, so the two read one source (ruling
# plan#2633); the app's configuration refuses to build without it
# (app/config.py, _ValkeyStore).
_load_secret SECRET_KEY secret_key
_load_secret APP_ROLE_PASSWORD app_role_password
_load_secret TOTP_ENCRYPTION_KEY totp_encryption_key
_load_secret TOTP_ENCRYPTION_KEY_OLD totp_encryption_key_old
_load_secret VALKEY_PASSWORD valkey_password

# ── 0a. Validate SECRET_KEY shape ─────────────────────────────────
# Flask's ProdConfig.__init__ also validates SECRET_KEY, but only once the
# server builds the app, and the dev stack's DevContainerConfig never does.
# Checking here stops the container before the server starts, with the
# Docker-secret hint below.  The placeholder list below
# must stay in sync with _KNOWN_DEFAULT_SECRETS in app/config.py.
# 32 is the minimum length matching _MIN_SECRET_KEY_LENGTH.
if [ -z "${SECRET_KEY:-}" ]; then
    echo "ERROR: SECRET_KEY is not set in the environment." >&2
    echo "       Generate with: python -c \"import secrets; print(secrets.token_hex(32))\"" >&2
    echo "       and add SECRET_KEY=<value> to .env before starting the app." >&2
    exit 1
fi
if [ "${#SECRET_KEY}" -lt 32 ]; then
    echo "ERROR: SECRET_KEY is shorter than 32 characters (got ${#SECRET_KEY})." >&2
    echo "       Generate a stronger key with: python -c \"import secrets; print(secrets.token_hex(32))\"" >&2
    exit 1
fi
case "${SECRET_KEY}" in
    dev-only-* | change-me-to-a-random-secret-key | dev-secret-key-not-for-production | replaced_by_docker_secret*)
        echo "ERROR: SECRET_KEY matches a known placeholder." >&2
        echo "       Replace it with a secure random value before starting the app." >&2
        # ``replaced_by_docker_secret*`` is the Commit C-38 placeholder
        # the runbook recommends operators leave in .env when
        # migrating to Docker secrets (Posture 2).  Hitting this arm
        # under that placeholder means _load_secret did NOT overwrite
        # SECRET_KEY -- the secret file at /run/secrets/secret_key is
        # absent or unreadable.  Surface a precise hint so the
        # operator does not have to re-derive that conclusion from a
        # generic "placeholder" diagnostic.
        case "${SECRET_KEY}" in
            replaced_by_docker_secret*)
                echo "       The .env placeholder is still in effect: the Docker" >&2
                echo "       secret at /run/secrets/secret_key is missing or" >&2
                echo "       unreadable.  See docs/runbook_secrets.md" >&2
                echo "       \"Migrating to Docker secrets\" for the file layout." >&2
                ;;
        esac
        exit 1
        ;;
esac

# ── 0b. Validate APP_ROLE_PASSWORD ───────────────────────────────
# The least-privilege ``shekel_app`` role's password is required so
# the runtime app can connect under DML-only credentials.  See audit
# finding F-081 / Commit C-13.  The password is opaque to this script
# -- we only check that it is set and not a placeholder.  A sensible
# minimum length deters accidental short or empty values; the
# operator can use any sufficiently random secret.
if [ -z "${APP_ROLE_PASSWORD:-}" ]; then
    echo "ERROR: APP_ROLE_PASSWORD is not set in the environment." >&2
    echo "       Generate with: python -c \"import secrets; print(secrets.token_urlsafe(32))\"" >&2
    echo "       and add APP_ROLE_PASSWORD=<value> to .env before starting the app." >&2
    exit 1
fi
if [ "${#APP_ROLE_PASSWORD}" -lt 16 ]; then
    echo "ERROR: APP_ROLE_PASSWORD is shorter than 16 characters (got ${#APP_ROLE_PASSWORD})." >&2
    echo "       Generate a stronger value with: python -c \"import secrets; print(secrets.token_urlsafe(32))\"" >&2
    exit 1
fi

# ── 1. Wait for PostgreSQL ──────────────────────────────────────
_wait_for_postgres shekel_app

# ── 2. Copy static files to shared volume ────────────────────────
# When a static_files volume is MOUNTED (bundled mode, where the
# in-stack nginx serves /static/ from it), the copy must succeed
# LOUDLY or fail LOUDLY -- a silent miss means stale assets with
# content-hash URLs pointing at bytes that no longer exist (polyglot
# audit 2026-06-12, OPS/SH-10: the previous `2>/dev/null || true`
# masked every failure mode and then printed success). When no volume
# is mounted, skip explicitly. The probe is /proc/mounts, NOT a bare
# -d test: the Dockerfile pre-creates the directory inside the image,
# so it always exists -- read-only and unmounted -- on a hardened dev
# rootfs (caught live by the Phase 2 battery: the -d form blocked the
# dev container's boot).
if grep -qs ' /var/www/static ' /proc/mounts; then
    echo "Copying static files to shared volume..."
    if ! cp -r /home/shekel/app/app/static/* /var/www/static/; then
        echo "ERROR: static file copy to /var/www/static failed" >&2
        echo "       (volume read-only? disk full?). Refusing to start with" >&2
        echo "       potentially stale static assets." >&2
        exit 1
    fi
    echo "Static files ready."
else
    echo "No /var/www/static volume mounted; skipping static copy (Flask serves static)."
fi

# ── 3. Start the application server ──────────────────────────────
# The web login's URL, the one the server connects by: app/config.py
# reads DATABASE_URL_APP first (scripts/_entrypoint_lib.sh builds it, with
# the TLS mode DB_SSLMODE names).
DATABASE_URL_APP="$(_database_url shekel_app "${APP_ROLE_PASSWORD}")"
export DATABASE_URL_APP

# exec "$@" runs the Dockerfile CMD (gunicorn in production, or the
# docker-compose command override in development).
echo "=== Starting Application ==="
exec "$@"
