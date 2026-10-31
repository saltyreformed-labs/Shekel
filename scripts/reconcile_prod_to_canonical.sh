#!/bin/bash
# Shekel Budget App -- One-shot production reconciliation.
#
# Reconciles /opt/docker/shekel/ to the repo's canonical state per
# /home/josh/.claude/plans/review-my-dev-docker-jiggly-quokka.md.
#
# Idempotent within reason -- skips steps whose output already exists.
# Run from the repo root.  After this completes the operator runs
# ``cd /opt/docker/shekel && docker compose down && docker compose up -d``
# to roll the stack.  This script intentionally STOPS short of that
# recreate so the operator has a chance to review the merged config
# (printed at the end of step 7) before pulling the trigger.
#
# Sudo is required ONLY for the cert chown (uid 70).  Everything else
# runs as the invoking user, relying on josh:josh ownership of
# /opt/docker/shekel/ and the docker group membership for ``docker``
# commands.
#
# Step-list:
#   1. Generate Postgres TLS cert (sudo for chown)
#   2. Create /opt/docker/shekel/{secrets,deploy/postgres} dirs
#   3. Migrate four high-sensitivity secrets from .env to files, and write
#      Valkey's password file from .env
#   4. Rewrite /opt/docker/shekel/.env (placeholders + new vars)
#   5. Create shekel-frontend network if missing
#   6. Update shared Nginx vhost (deploy/nginx-shared/conf.d/shekel.conf)
#   7. Copy repo compose files and deploy/valkey/ to /opt/docker/shekel/
#
# Exit codes:
#   0 -- all steps succeeded; operator may proceed with recreate.
#   1 -- a precondition failed; nothing was changed past the failure.
#   2 -- a destructive step refused because the input was unsafe.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROD_ROOT=/opt/docker/shekel
SHARED_NGINX_CONF_D=/opt/docker/nginx/conf.d

log() { printf '\n\033[1;36m== %s ==\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARN: %s\033[0m\n' "$*" >&2; }
die() {
    printf '\033[1;31mFATAL: %s\033[0m\n' "$*" >&2
    exit 1
}

# Sanity: run from repo root.
[[ -f "$REPO_ROOT/docker-compose.yml" ]] || die "must be invoked from the Shekel repo (cwd=$REPO_ROOT)"
[[ -f "$REPO_ROOT/deploy/docker-compose.prod.yml" ]] || die "deploy/docker-compose.prod.yml missing"
[[ -d "$PROD_ROOT" ]] || die "$PROD_ROOT does not exist; production not laid out as expected"

# ── 1. Generate Postgres TLS cert ─────────────────────────────────
log "Step 1: Postgres TLS cert"
mkdir -p "$PROD_ROOT/deploy/postgres"
if [[ -f "$PROD_ROOT/deploy/postgres/server.crt" && -f "$PROD_ROOT/deploy/postgres/server.key" ]]; then
    warn "cert + key already present at $PROD_ROOT/deploy/postgres/ -- skipping generation"
else
    # The script writes to ./deploy/postgres relative to the cwd it
    # is invoked from.  We chdir to PROD_ROOT so the output lands at
    # the path the canonical override mounts (./deploy/postgres/
    # relative to /opt/docker/shekel/docker-compose.override.yml).
    (cd "$PROD_ROOT" && sudo "$REPO_ROOT/scripts/generate_pg_cert.sh" --output-dir "$PROD_ROOT/deploy/postgres")
fi

# ── 2. Create secrets directory ───────────────────────────────────
log "Step 2: secrets directory"
if [[ ! -d "$PROD_ROOT/secrets" ]]; then
    install -d -m 0700 "$PROD_ROOT/secrets"
fi
[[ -d "$PROD_ROOT/secrets" ]] || die "could not create $PROD_ROOT/secrets"
chmod 0700 "$PROD_ROOT/secrets"

# ── 3. Migrate four secrets from .env to files ────────────────────
log "Step 3: migrate secrets to files"
extract_env_value() {
    # Echo the key's value, or EMPTY when the line is absent -- never a
    # non-zero status. Under set -e + pipefail a bare grep-miss here used
    # to kill the whole script mid-run with no diagnostic, AFTER step 3
    # had already migrated secrets (OPS/SH-12); required keys get their
    # explicit die at the call sites instead.
    #
    # `sed -n '1p'`, not `head -1` (plan step balance:X-dm): sed reads to
    # the end, so no stage can be killed writing to a reader that left,
    # which pipefail would report as a failure.  The `|| true` answers
    # grep's no-match, and also hid that race: cut had printed the value.
    local key=$1
    grep -E "^${key}=" "$PROD_ROOT/.env" | sed -n '1p' | cut -d= -f2- || true
}

for pair in \
    "SECRET_KEY:secret_key" \
    "POSTGRES_PASSWORD:postgres_password" \
    "APP_ROLE_PASSWORD:app_role_password" \
    "TOTP_ENCRYPTION_KEY:totp_encryption_key"; do
    env_key=${pair%%:*}
    file_name=${pair##*:}
    target="$PROD_ROOT/secrets/$file_name"
    if [[ -s "$target" ]]; then
        warn "$target already non-empty -- skipping"
        continue
    fi
    value=$(extract_env_value "$env_key")
    if [[ -z "$value" ]]; then
        die "$env_key missing or empty in $PROD_ROOT/.env -- cannot migrate to file"
    fi
    # ``printf '%s'`` writes the exact bytes with no trailing newline.
    # The entrypoint's _load_secret helper strips a trailing newline
    # for forgiveness, but printf-style is the documented form.
    # umask-first so the file is NEVER world-readable, even for the
    # instant before chmod (OPS/SH-22).
    (
        umask 077
        printf '%s' "$value" >"$target"
    )
    chmod 0600 "$target"
    # shellcheck disable=SC2312 # wc -c is a display-only byte count for the wrote message; the file was just written (the preceding chmod would have aborted on a missing file)
    echo "wrote $target ($(wc -c <"$target") bytes)"
done

# Valkey's password file, which the override's valkey service reads.
# Unlike the four above it stays real in .env too: the app's
# RATELIMIT_STORAGE_URI interpolates SHEKEL_VALKEY_PASSWORD until
# plan#2168 moves the app onto this file.  0640, not 0600: the valkey
# container reads it as uid 999 gid 1000, gid 1000 is the operating
# user's group, and compose v2 ignores the secret reference's mode.
valkey_target="$PROD_ROOT/secrets/valkey_password"
if [[ -s "$valkey_target" ]]; then
    warn "$valkey_target already non-empty -- skipping"
else
    value=$(extract_env_value SHEKEL_VALKEY_PASSWORD)
    [[ -n "$value" ]] || die "SHEKEL_VALKEY_PASSWORD missing or empty in $PROD_ROOT/.env -- cannot write $valkey_target"
    (
        umask 027
        printf '%s' "$value" >"$valkey_target"
    )
    chmod 0640 "$valkey_target"
    # shellcheck disable=SC2312 # wc -c is a display-only byte count for the wrote message, as in the loop above
    echo "wrote $valkey_target ($(wc -c <"$valkey_target") bytes)"
fi

# ── 4. Rewrite /opt/docker/shekel/.env ────────────────────────────
log "Step 4: rewrite $PROD_ROOT/.env"

# Carry the current image digest forward unchanged -- we are NOT
# image-bumping as part of this reconciliation. Post-C-36 the digest
# lives in .env as SHEKEL_IMAGE_DIGEST (the canonical layout this script
# itself installs); the grep fallback covers only the legacy layout
# where the digest sat inline on the shekel image line. The original
# primary pipeline here was dead logic -- grep -oE emits bare digests,
# so the downstream `grep -A1 saltyreformed` could never match -- and
# its fallback died under set -e BEFORE the crafted die() could run
# when nothing matched (OPS/SH-11). NOTE the fallback greps the SHEKEL
# image line specifically: a bare 'first sha256 in the file' would now
# match the postgres digest pin.
compose_image_digest() {
    # Echo the first digest on a SHEKEL image line of the legacy compose
    # layout, or EMPTY -- never a non-zero status; the caller's die names
    # both places it looked.  Ends in `sed -n '1p'`, not `head -1` (plan
    # step balance:X-dm), for the reason extract_env_value gives.
    grep -E 'image:.*saltyreformed/shekel' "$PROD_ROOT/docker-compose.yml" \
        | grep -oE 'sha256:[0-9a-f]{64}' | sed -n '1p' || true
}
current_digest=$(extract_env_value SHEKEL_IMAGE_DIGEST)
if [[ -z "$current_digest" ]]; then
    current_digest=$(compose_image_digest)
fi
[[ -n "$current_digest" ]] || die "could not determine the image digest: no SHEKEL_IMAGE_DIGEST in $PROD_ROOT/.env and no inline digest on the shekel image line of $PROD_ROOT/docker-compose.yml"

# Carry forward the values that should survive: SHEKEL_VALKEY_PASSWORD,
# SEED_USER_*, GUNICORN_WORKERS, NGINX_PORT, AUDIT_RETENTION_DAYS,
# LOG_LEVEL, SLOW_REQUEST_THRESHOLD_MS, TOTP_ENCRYPTION_KEY_OLD.
SHEKEL_VALKEY_PASSWORD=$(extract_env_value SHEKEL_VALKEY_PASSWORD)
[[ -n "$SHEKEL_VALKEY_PASSWORD" ]] || die "SHEKEL_VALKEY_PASSWORD missing in current .env"
SEED_USER_EMAIL=$(extract_env_value SEED_USER_EMAIL)
SEED_USER_PASSWORD=$(extract_env_value SEED_USER_PASSWORD)
SEED_USER_DISPLAY_NAME=$(extract_env_value SEED_USER_DISPLAY_NAME)
GUNICORN_WORKERS=$(extract_env_value GUNICORN_WORKERS)
NGINX_PORT=$(extract_env_value NGINX_PORT)
AUDIT_RETENTION_DAYS=$(extract_env_value AUDIT_RETENTION_DAYS)
LOG_LEVEL=$(extract_env_value LOG_LEVEL)
SLOW_REQUEST_THRESHOLD_MS=$(extract_env_value SLOW_REQUEST_THRESHOLD_MS)

# Back up the existing .env before rewriting.
backup=$PROD_ROOT/.env.bak.$(date +%Y%m%d-%H%M%S)
cp "$PROD_ROOT/.env" "$backup"
chmod 0600 "$backup"
echo "backed up to $backup"

# shellcheck disable=SC2312 # the only command-sub in this heredoc is the date -u call on the comment line, which always succeeds and only stamps a timestamp into a .env comment
cat >"$PROD_ROOT/.env" <<ENVEOF
# Shekel Budget App -- shared-mode production environment.
# Rewritten by scripts/reconcile_prod_to_canonical.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ).
# Real secret values for SECRET_KEY, POSTGRES_PASSWORD,
# APP_ROLE_PASSWORD, and TOTP_ENCRYPTION_KEY are now in
# /opt/docker/shekel/secrets/<name>; the placeholders below satisfy
# the base docker-compose.yml's \${VAR:?...} interpolation.
# Audit findings F-022, F-109, F-148 / Commits C-34, C-38.

SECRET_KEY=replaced_by_docker_secret
POSTGRES_PASSWORD=replaced_by_docker_secret
APP_ROLE_PASSWORD=replaced_by_docker_secret
TOTP_ENCRYPTION_KEY=replaced_by_docker_secret

# Retired TOTP keys for non-destructive rotation.  Empty in steady
# state.  When set, also write the value to
# /opt/docker/shekel/secrets/totp_encryption_key_old and inline-add
# it to the secrets: block of the override during the rotation
# window.  See docs/runbook_secrets.md "Rotating TOTP_ENCRYPTION_KEY".
TOTP_ENCRYPTION_KEY_OLD=

# Per-app Valkey ACL user password.  Lower sensitivity than the four
# secrets above (gates a Valkey user restricted to the limiter's own
# commands on the LIMITS:* keys, on an internal network).  The valkey
# service reads it from /opt/docker/shekel/secrets/valkey_password; the
# app's RATELIMIT_STORAGE_URI still reads it here, so the two must
# match until plan#2168.
SHEKEL_VALKEY_PASSWORD=$SHEKEL_VALKEY_PASSWORD

# Image digest pin.  Carried forward from the previous .env unchanged
# so this reconciliation does not double as an image bump.  Rotate
# via deploy/README.md "Image digest pinning" workflow.
SHEKEL_IMAGE_DIGEST=$current_digest

# Public-registration toggle.  Default false in production (audit
# finding F-053): the single-user budget app should not ship with
# an open registration surface.  /register returns 404 and the
# registration link is hidden on /login when this is false.
REGISTRATION_ENABLED=false

# Seed user (first-boot only; .env values are read once then scrubbed
# from the entrypoint's shell env).
SEED_USER_EMAIL=$SEED_USER_EMAIL
SEED_USER_PASSWORD=$SEED_USER_PASSWORD
SEED_USER_DISPLAY_NAME=$SEED_USER_DISPLAY_NAME

# Gunicorn / Nginx / observability tuneables.
GUNICORN_WORKERS=${GUNICORN_WORKERS:-2}
NGINX_PORT=${NGINX_PORT:-80}
AUDIT_RETENTION_DAYS=${AUDIT_RETENTION_DAYS:-365}
LOG_LEVEL=${LOG_LEVEL:-INFO}
SLOW_REQUEST_THRESHOLD_MS=${SLOW_REQUEST_THRESHOLD_MS:-500}
ENVEOF
chmod 0600 "$PROD_ROOT/.env"
echo "rewrote $PROD_ROOT/.env (backup at $backup)"

# ── 5. shekel-frontend network ────────────────────────────────────
log "Step 5: shekel-frontend network"
if docker network inspect shekel-frontend >/dev/null 2>&1; then
    echo "shekel-frontend already exists"
else
    docker network create shekel-frontend --driver bridge --subnet 172.32.0.0/24
fi

# ── 6. Shared Nginx vhost ─────────────────────────────────────────
log "Step 6: shared Nginx vhost"
# Snapshot the host nginx files BEFORE overwriting them.  The repo
# nginx-shared/nginx.conf has historically drifted behind the host
# (e.g. Audit B7 hardening added on the host first), so an unconditional
# repo->host sync can clobber load-bearing config.  Snapshot first,
# diff second, copy third.
nginx_snap=/opt/docker/nginx/.reconcile-snapshot-$(date +%Y%m%d-%H%M%S)
# 0700: snapshot dirs hold pre-reconcile copies of live config; keep
# them owner-only so residue can never widen the exposure of whatever
# it captured (parity audit 2026-06-12, finding M08 -- the 2026-05-14
# run left a world-readable rendered config embedding a credential).
install -d -m 0700 "$nginx_snap"
cp "$SHARED_NGINX_CONF_D/shekel.conf" "$nginx_snap/shekel.conf.pre-reconcile" 2>/dev/null || true
cp /opt/docker/nginx/nginx.conf "$nginx_snap/nginx.conf.pre-reconcile"
echo "nginx snapshot saved to $nginx_snap/"

if cmp -s "$REPO_ROOT/deploy/nginx-shared/conf.d/shekel.conf" "$SHARED_NGINX_CONF_D/shekel.conf"; then
    echo "$SHARED_NGINX_CONF_D/shekel.conf already matches repo -- skipping"
else
    cp -v "$REPO_ROOT/deploy/nginx-shared/conf.d/shekel.conf" "$SHARED_NGINX_CONF_D/shekel.conf"
    echo "(operator: validate + reload nginx after step 7)"
fi

# Also sync the main nginx.conf if it differs -- and FAIL LOUDLY (for
# real now -- OPS/SH-13: the previous revision PROMISED this gate in a
# comment while unconditionally cp-ing over the host file) when the host
# file carries critical directives the repo copy lacks: that is host-only
# hardening a sync would clobber, the exact drift mode the deploy rules
# warn about. Back-port to the repo first, then re-run.
if cmp -s "$REPO_ROOT/deploy/nginx-shared/nginx.conf" /opt/docker/nginx/nginx.conf; then
    echo "/opt/docker/nginx/nginx.conf already matches repo"
else
    drift_dir=$nginx_snap/divergence
    install -d -m 0700 "$drift_dir"
    diff -u /opt/docker/nginx/nginx.conf "$REPO_ROOT/deploy/nginx-shared/nginx.conf" >"$drift_dir/nginx.conf.diff" || true
    echo "Drift between host and repo nginx.conf saved to $drift_dir/nginx.conf.diff"
    critical_re="limit_req_zone|limit_conn_zone|client_body_timeout|client_header_timeout|send_timeout|client_max_body_size|set_real_ip_from"
    host_only_critical=$(grep -E "$critical_re" /opt/docker/nginx/nginx.conf | sort -u | comm -23 - <(grep -E "$critical_re" "$REPO_ROOT/deploy/nginx-shared/nginx.conf" | sort -u) || true)
    if [[ -n "$host_only_critical" ]]; then
        echo "HOST-ONLY critical directives that the sync would clobber:"
        printf '%s\n' "$host_only_critical"
        die "host nginx.conf carries hardening the repo copy lacks; back-port to deploy/nginx-shared/nginx.conf first (diff at $drift_dir/nginx.conf.diff)"
    fi
    cp -v "$REPO_ROOT/deploy/nginx-shared/nginx.conf" /opt/docker/nginx/nginx.conf
fi

# ── 7. Copy compose files ─────────────────────────────────────────
log "Step 7: compose files"
# Snapshot the old files for rollback before overwriting.
snap_dir=$PROD_ROOT/.reconcile-snapshot-$(date +%Y%m%d-%H%M%S)
# 0700 for the same M08 rationale as the nginx snapshot above.
install -d -m 0700 "$snap_dir"
cp "$PROD_ROOT/docker-compose.yml" "$snap_dir/docker-compose.yml.pre-reconcile"
cp "$PROD_ROOT/docker-compose.override.yml" "$snap_dir/docker-compose.override.yml.pre-reconcile"
echo "snapshot saved to $snap_dir/"

cp -v "$REPO_ROOT/docker-compose.yml" "$PROD_ROOT/docker-compose.yml"
cp -v "$REPO_ROOT/deploy/docker-compose.prod.yml" "$PROD_ROOT/docker-compose.override.yml"
# Valkey's ACL rules and start script, which the base file mounts from
# ./deploy/valkey (it refuses to start without them).
if [[ -d "$PROD_ROOT/deploy/valkey" ]]; then
    cp -a "$PROD_ROOT/deploy/valkey" "$snap_dir/valkey.pre-reconcile"
fi
install -d -m 0755 "$PROD_ROOT/deploy/valkey"
cp -v "$REPO_ROOT/deploy/valkey/users.acl" "$REPO_ROOT/deploy/valkey/start.sh" "$PROD_ROOT/deploy/valkey/"

# Validate the merged config before exiting.  The rendered merge
# INTERPOLATES .env values -- any credential that is not yet a docker
# secret (e.g. SHEKEL_VALKEY_PASSWORD inside the bundled valkey
# service's VALKEY_PASSWORD and the app's RATELIMIT_STORAGE_URI
# default) appears in cleartext -- so
# the file must be owner-only from the moment it exists (umask first,
# then chmod is belt-and-braces; finding M08).
log "Validation: docker compose config (merged)"
(umask 077 && cd "$PROD_ROOT" && docker compose config >"$snap_dir/merged-config.yml")
chmod 0600 "$snap_dir/merged-config.yml"
echo "merged config rendered to $snap_dir/merged-config.yml"

# Sanity-spot key fields.
grep -qE '^    read_only: true$' "$snap_dir/merged-config.yml" || die "read_only field missing in merged config"
grep -qE '^      shekel-frontend:' "$snap_dir/merged-config.yml" || die "shekel-frontend network not attached in merged config"
grep -qE '^secrets:$' "$snap_dir/merged-config.yml" || die "secrets block missing in merged config"

cat <<EONEXT

────────────────────────────────────────────────────────────────────
RECONCILIATION COMPLETE -- pending recreate.

Spot-check the merged config:
    less $snap_dir/merged-config.yml

When ready to recreate the stack:

    # (a) Recreate the shared Nginx + cloudflared to pick up the
    #     shekel-frontend network attachment from /opt/docker/docker-compose.yml.
    cd /opt/docker
    docker compose up -d nginx cloudflared

    # (b) Validate Nginx is happy with the new vhost before reload.
    docker exec nginx nginx -t
    docker exec nginx nginx -s reload

    # (c) Recreate the Shekel stack.
    cd $PROD_ROOT
    docker compose down
    docker compose up -d

    # (d) Confirm health.
    docker compose ps
    docker compose logs --tail=80 app
    docker inspect shekel-prod-app --format '{{.HostConfig.ReadonlyRootfs}}'  # → true
    docker inspect shekel-prod-app --format '{{.HostConfig.CapDrop}}'         # → [ALL]
    curl -sI https://shekel.saltyreformed.com/login | sed -n '1,5p'            # → 200 + sec headers

Rollback (if anything goes wrong):

    sudo cp $snap_dir/docker-compose.yml.pre-reconcile $PROD_ROOT/docker-compose.yml
    sudo cp $snap_dir/docker-compose.override.yml.pre-reconcile $PROD_ROOT/docker-compose.override.yml
    # and, if it existed before, deploy/valkey from $snap_dir/valkey.pre-reconcile
    sudo cp $backup $PROD_ROOT/.env
    cd $PROD_ROOT && docker compose up -d
────────────────────────────────────────────────────────────────────
EONEXT
