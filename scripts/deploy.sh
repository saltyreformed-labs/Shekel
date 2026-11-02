#!/bin/bash
# Shekel Budget App -- SELF-HOSTED Source-Build Deployment Script
#
# SCOPE (polyglot audit 2026-06-12, OPS/SH-14): this script serves
# self-hosted installs that BUILD FROM SOURCE (docker-compose.yml +
# docker-compose.build.yml). It is NOT the maintainer's production
# deploy path -- that is the digest-pinned, cosign-keyless-verified
# /opt/docker/scripts/shekel-deploy.sh pipeline driving the hardened
# stack at /opt/docker/shekel. Running this script on a host where
# that canonical stack exists would replace the pinned, hardened
# container with an unpinned locally-built one, so it now REFUSES to
# run when /opt/docker/shekel exists (override: --force-self-hosted).
#
# Pulls the latest code from GitHub, builds the Docker image, restarts
# the application container, verifies health, and rolls back to the
# previous image if the health check fails.
#
# Migrations run automatically via entrypoint.sh on container start.
# No manual migration step is needed.
#
# Usage:
#     ./scripts/deploy.sh [OPTIONS]
#
# Options:
#     --skip-pull         Skip git pull (deploy from current working tree)
#     --skip-backup       Skip pre-deploy database backup
#     --health-timeout N  Seconds to wait for health check (default: 60)
#     --health-interval N Seconds between health check retries (default: 5)
#     --help              Show this help message
#
# Exit codes:
#     0   Deployment completed successfully
#     1   Fatal error (see log output for details)
#
# Prerequisites:
#     - Docker and Docker Compose installed
#     - Git repository cloned to DEPLOY_DIR
#     - .env file configured (see .env.example)
#     - NAS mounted if using backup before deploy

set -euo pipefail

# ── Configuration ────────────────────────────────────────────────
# All values can be overridden via environment variables.

DEPLOY_DIR="${DEPLOY_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
APP_CONTAINER="${APP_CONTAINER:-shekel-prod-app}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-60}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-5}"
NGINX_PORT="${NGINX_PORT:-80}"
HEALTH_URL="http://localhost:${NGINX_PORT}/health"
# Array, not a space-joined string: the callers expand it unquoted
# otherwise -- the precise unquoted-expansion pattern the shell
# standards ban (OPS/SH-19).
COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.build.yml)

# Image reference used by Cosign sign + verify steps.  Matches the
# tag the local build produces (the COMPOSE_FILES merge tags the
# image as localhost/shekel:latest, the build's own name, which
# docker-compose.build.yml gives it under ruling plan#2703).  Cosign
# signs the image at the digest the local build produced; the digest itself
# is captured at sign time and printed for the operator to paste
# into /opt/docker/shekel/.env (SHEKEL_IMAGE_DIGEST).  Neither holds for
# an image only this host's store has, which no registry serves to sign
# or to verify: step plan#2705 decides what a source build's check is.
IMAGE_REF="${IMAGE_REF:-localhost/shekel:latest}"

# Cosign integration (audit findings F-060, F-155 / Commit C-36).
# COSIGN_PUBLIC_KEY: path to the verifier key (committed to repo at
#   deploy/cosign.pub).  When this file exists, sign + verify run on
#   every deploy; when it is absent, the script logs a warning and
#   continues so a fresh checkout without a generated keypair still
#   completes a deploy.
# COSIGN_PRIVATE_KEY: path to the signing key.  Out of repo by
#   default (chmod 600 in /etc/shekel/cosign.key); operators can
#   override via .env or the host's exported env.
# COSIGN_REQUIRED: when ``true``, missing cosign or missing key
#   FAILS the deploy.  Default ``false`` so a first-run bring-up
#   without cosign still completes.
COSIGN_PUBLIC_KEY="${COSIGN_PUBLIC_KEY:-${DEPLOY_DIR}/deploy/cosign.pub}"
COSIGN_PRIVATE_KEY="${COSIGN_PRIVATE_KEY:-/etc/shekel/cosign.key}"
COSIGN_REQUIRED="${COSIGN_REQUIRED:-false}"

# Flags (overridden by command-line options).
SKIP_PULL=false
SKIP_BACKUP=false
SKIP_COSIGN=false
FORCE_SELF_HOSTED=false

# ── Functions ────────────────────────────────────────────────────

log() {
    # Structured log output: [YYYY-MM-DD HH:MM:SS] [LEVEL] message
    local level="$1"
    shift
    # shellcheck disable=SC2312 # date with a fixed format string always succeeds; this is log-line formatting, not a checked command.
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [${level}] $*"
}

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Deploy the latest version of the Shekel budget app.

Workflow:
  1. Pull latest code from GitHub
  2. Back up the database (optional)
  3. Tag the current Docker image for rollback
  4. Build the new Docker image
  5. Sign the new image with Cosign (audit Commit C-36)
  6. Verify the signature before swapping the running container
  7. Restart the app container (migrations run automatically)
  8. Wait for the health endpoint to report healthy
  9. Roll back to the previous image if health check fails

Options:
    --skip-pull         Skip git pull (deploy from current working tree)
    --skip-backup       Skip pre-deploy database backup
    --skip-cosign       Skip Cosign sign + verify (use only in emergencies)
    --require-cosign    Fail the deploy when cosign/keys are missing
                        (equivalent to COSIGN_REQUIRED=true)
    --force-self-hosted Run even though /opt/docker/shekel exists (see header)
    --health-timeout N  Seconds to wait for health check (default: 60)
    --health-interval N Seconds between health check retries (default: 5)
    --help              Show this help message

Environment Variables:
    DEPLOY_DIR          Repository root directory (default: script parent dir)
    APP_CONTAINER       App container name (default: shekel-prod-app)
    HEALTH_TIMEOUT      Health check timeout in seconds (default: 60)
    HEALTH_INTERVAL     Health check retry interval in seconds (default: 5)
    NGINX_PORT          Nginx host port for health check URL (default: 80)
    IMAGE_REF           Image reference passed to cosign sign/verify
                        (default: localhost/shekel:latest)
    COSIGN_PUBLIC_KEY   Verifier key path (default: deploy/cosign.pub)
    COSIGN_PRIVATE_KEY  Signing key path (default: /etc/shekel/cosign.key)
    COSIGN_REQUIRED     When 'true', missing cosign/key fails the deploy
                        instead of warning (default: false)
EOF
}

check_prerequisites() {
    # Verify required tools are available.
    local missing=false

    if ! command -v docker &>/dev/null; then
        log "ERROR" "docker command not found"
        missing=true
    fi

    if ! command -v git &>/dev/null; then
        log "ERROR" "git command not found"
        missing=true
    fi

    if ! command -v curl &>/dev/null; then
        log "ERROR" "curl command not found"
        missing=true
    fi

    if [ "$missing" = true ]; then
        log "ERROR" "Missing prerequisites. Install the required tools and retry."
        exit 1
    fi

    # Verify we're in a git repository.
    if [ ! -d "${DEPLOY_DIR}/.git" ]; then
        log "ERROR" "Not a git repository: ${DEPLOY_DIR}"
        exit 1
    fi

    # Verify .env file exists.
    if [ ! -f "${DEPLOY_DIR}/.env" ]; then
        log "ERROR" ".env file not found in ${DEPLOY_DIR}"
        log "ERROR" "Copy .env.example to .env and configure it before deploying."
        exit 1
    fi

    # Verify docker compose is available (v2 plugin syntax).
    if ! docker compose version &>/dev/null; then
        log "ERROR" "docker compose (v2) not found. Install the Docker Compose plugin."
        exit 1
    fi
}

pull_latest() {
    # Pull the latest code from the remote repository.
    log "INFO" "Pulling latest code from GitHub..."
    cd "${DEPLOY_DIR}"

    if ! git pull --ff-only; then
        log "ERROR" "git pull failed. Resolve conflicts or network issues and retry."
        exit 1
    fi

    # shellcheck disable=SC2312 # HEAD is resolvable after the preceding ff-only pull succeeded; the short hash is used only for the display log line.
    log "INFO" "Code updated to $(git rev-parse --short HEAD)"
}

backup_database() {
    # Run a pre-deploy database backup.
    local backup_script="${DEPLOY_DIR}/scripts/backup.sh"

    if [ ! -x "$backup_script" ]; then
        log "WARNING" "Backup script not found or not executable: ${backup_script}"
        log "WARNING" "Skipping pre-deploy backup."
        return 0
    fi

    log "INFO" "Running pre-deploy database backup..."
    if ! "$backup_script"; then
        log "ERROR" "Pre-deploy backup failed. Aborting deployment."
        log "ERROR" "Fix the backup issue before deploying, or use --skip-backup."
        exit 1
    fi

    log "INFO" "Pre-deploy backup completed."
}

tag_previous_image() {
    # Tag the current running image as :previous for rollback.
    log "INFO" "Tagging current image for rollback..."

    # Get the image ID of the currently running app container.
    local current_image
    current_image=$(docker inspect --format='{{.Image}}' "${APP_CONTAINER}" 2>/dev/null || true)

    if [ -z "$current_image" ]; then
        log "WARNING" "No running ${APP_CONTAINER} container found. Skipping image tag."
        log "WARNING" "This is expected on first deployment."
        return 0
    fi

    # Tag it as :previous so we can restore it on rollback.
    docker tag "${current_image}" "${APP_CONTAINER}:previous"
    log "INFO" "Tagged current image as ${APP_CONTAINER}:previous"
}

build_image() {
    # Build the new Docker image.
    log "INFO" "Building new Docker image..."
    cd "${DEPLOY_DIR}"

    if ! docker compose "${COMPOSE_FILES[@]}" build app; then
        log "ERROR" "Docker image build failed. The previous version is still running."
        exit 1
    fi

    log "INFO" "Docker image built successfully."
}

cosign_available() {
    # Return 0 when cosign is on PATH, 1 otherwise.  Used by the
    # sign/verify wrappers below to decide whether to enforce or
    # downgrade to a warning.
    command -v cosign &>/dev/null
}

cosign_signing_key_present() {
    # Return 0 when both the private (signing) key and public
    # (verifier) key files exist and are readable.  We test both
    # because signing without a matching verifier means the next
    # deploy cannot verify; better to fail fast on the operator
    # config gap than emit an unverifiable signature.
    [ -f "${COSIGN_PRIVATE_KEY}" ] && [ -r "${COSIGN_PRIVATE_KEY}" ] \
        && [ -f "${COSIGN_PUBLIC_KEY}" ] && [ -r "${COSIGN_PUBLIC_KEY}" ]
}

cosign_verifier_key_present() {
    # Return 0 when only the verifier (public) key exists -- enough
    # for the verify step (which never reads the private key).
    [ -f "${COSIGN_PUBLIC_KEY}" ] && [ -r "${COSIGN_PUBLIC_KEY}" ]
}

cosign_resolve_digest() {
    # Print the local image's digest (sha256:...) to stdout, or
    # empty on failure.  ``docker inspect`` reads the image's
    # RepoDigests when present (i.e. after a push); for a freshly
    # built local image, .Id is the manifest digest the local store
    # assigned and is what cosign will sign.
    #
    # The first line by parameter expansion, not `| head -n 1` (plan step
    # balance:X-dm): no reader can leave the pipe early, and `|| true`
    # makes a failed inspect EMPTY here by construction, not because the
    # callers' command substitution happens to run without errexit.
    local digest
    digest=$(docker inspect --format='{{.Id}}' "${IMAGE_REF}" 2>/dev/null || true)
    digest=${digest%%$'\n'*}
    # Normalise to bare ``sha256:...`` (docker may print the digest
    # under .Id without the algorithm prefix on some versions).
    if [ -n "$digest" ] && [[ "$digest" != sha256:* ]]; then
        digest="sha256:${digest}"
    fi
    echo "$digest"
}

handle_cosign_skip() {
    # Centralised "no cosign / no key" handler so sign and verify
    # apply the same fail-vs-warn policy.  Emits a remediation
    # pointer and respects COSIGN_REQUIRED + --skip-cosign.
    local reason="$1"

    if [ "${SKIP_COSIGN}" = true ]; then
        log "WARNING" "${reason}; --skip-cosign was passed, continuing."
        return 0
    fi

    if [ "${COSIGN_REQUIRED}" = "true" ]; then
        log "ERROR" "${reason}; COSIGN_REQUIRED=true."
        log "ERROR" "Install cosign and generate a keypair (see"
        log "ERROR" "deploy/README.md \"Image digest pinning\"), or"
        log "ERROR" "rerun with --skip-cosign to bypass for this deploy."
        return 1
    fi

    log "WARNING" "${reason}; continuing without signature controls."
    log "WARNING" "Set COSIGN_REQUIRED=true in .env or pass --require-cosign"
    log "WARNING" "to promote this warning to an error in steady-state ops."
    # (--require-cosign exists since the OPS/SH-18 fix; it simply sets
    # COSIGN_REQUIRED=true for this run.)
    return 0
}

sign_image() {
    # Sign the locally-built image with the maintainer's Cosign
    # keypair so the verify step (and any downstream operator) can
    # confirm the running image matches what was built on this host.
    # Audit finding F-155 / Commit C-36.
    log "INFO" "Signing image with Cosign..."

    # shellcheck disable=SC2310 # cosign_available is a boolean predicate (wraps command -v); its 0/1 is exactly what this if tests, no internal command needs to abort the script.
    if ! cosign_available; then
        # shellcheck disable=SC2310 # handle_cosign_skip's return is the policy decision (1 only when COSIGN_REQUIRED); the || exit 1 fully routes that status, nothing is swallowed.
        handle_cosign_skip "cosign not installed (sign step)" || exit 1
        return 0
    fi

    # shellcheck disable=SC2310 # cosign_signing_key_present is a boolean predicate ([ -f ]/[ -r ] tests); its 0/1 is exactly what this if tests.
    if ! cosign_signing_key_present; then
        # shellcheck disable=SC2310 # handle_cosign_skip's return is the policy decision (1 only when COSIGN_REQUIRED); the || exit 1 fully routes that status.
        handle_cosign_skip \
            "cosign keypair missing at COSIGN_PRIVATE_KEY=${COSIGN_PRIVATE_KEY} or COSIGN_PUBLIC_KEY=${COSIGN_PUBLIC_KEY}" \
            || exit 1
        return 0
    fi

    local digest
    digest=$(cosign_resolve_digest)
    if [ -z "$digest" ]; then
        log "ERROR" "Could not resolve digest for ${IMAGE_REF}; cannot sign."
        log "ERROR" "Verify 'docker images ${IMAGE_REF}' lists the image."
        exit 1
    fi

    # Sign by digest (cosign best practice -- signing by tag would
    # let a tag swap point the signature at a different image
    # silently).  COSIGN_PASSWORD must be exported by the operator
    # before invoking deploy.sh; the script never prompts because
    # the deploy is intended to run unattended once initiated.
    if ! COSIGN_PASSWORD="${COSIGN_PASSWORD:-}" cosign sign --yes \
        --key "${COSIGN_PRIVATE_KEY}" \
        "${IMAGE_REF}@${digest}"; then
        log "ERROR" "cosign sign failed for ${IMAGE_REF}@${digest}."
        log "ERROR" "Check COSIGN_PASSWORD env var (required for"
        log "ERROR" "encrypted private keys) and the key permissions."
        exit 1
    fi

    log "INFO" "Image signed: ${IMAGE_REF}@${digest}"
    log "INFO" "Update SHEKEL_IMAGE_DIGEST=${digest} in /opt/docker/shekel/.env"
    log "INFO" "(see deploy/README.md \"Image digest pinning\")."
}

verify_image_signature() {
    # Verify the signature on the image about to be deployed.  Runs
    # AFTER sign_image so the verifier exercises the just-produced
    # signature -- catches a signing-key/verifier-key mismatch
    # before the container swap.  Audit finding F-155 / Commit C-36.
    log "INFO" "Verifying image signature with Cosign..."

    # shellcheck disable=SC2310 # cosign_available is a boolean predicate (wraps command -v); its 0/1 is exactly what this if tests, no internal command needs to abort the script.
    if ! cosign_available; then
        # shellcheck disable=SC2310 # handle_cosign_skip's return is the policy decision (1 only when COSIGN_REQUIRED); the || exit 1 fully routes that status, nothing is swallowed.
        handle_cosign_skip "cosign not installed (verify step)" || exit 1
        return 0
    fi

    # shellcheck disable=SC2310 # cosign_verifier_key_present is a boolean predicate ([ -f ]/[ -r ] tests); its 0/1 is exactly what this if tests.
    if ! cosign_verifier_key_present; then
        # shellcheck disable=SC2310 # handle_cosign_skip's return is the policy decision (1 only when COSIGN_REQUIRED); the || exit 1 fully routes that status.
        handle_cosign_skip \
            "cosign verifier key missing at COSIGN_PUBLIC_KEY=${COSIGN_PUBLIC_KEY}" \
            || exit 1
        return 0
    fi

    local digest
    digest=$(cosign_resolve_digest)
    if [ -z "$digest" ]; then
        log "ERROR" "Could not resolve digest for ${IMAGE_REF}; cannot verify."
        exit 1
    fi

    if ! cosign verify --key "${COSIGN_PUBLIC_KEY}" \
        "${IMAGE_REF}@${digest}" >/dev/null 2>&1; then
        log "ERROR" "cosign verify FAILED for ${IMAGE_REF}@${digest}."
        log "ERROR" "The locally-built image is unsigned or its signature"
        log "ERROR" "does not match COSIGN_PUBLIC_KEY=${COSIGN_PUBLIC_KEY}."
        log "ERROR" "Refusing to deploy an unverifiable image."
        exit 1
    fi

    log "INFO" "Signature verified: ${IMAGE_REF}@${digest}"
}

restart_app() {
    # Restart the app container with the new image.
    # Migrations run automatically via entrypoint.sh.
    log "INFO" "Restarting application container..."
    cd "${DEPLOY_DIR}"

    # Recreate the app container with the new image.
    # --no-deps: only restart the app, not db or nginx.
    # The db and nginx containers remain running.
    if ! docker compose "${COMPOSE_FILES[@]}" up -d --no-deps --force-recreate app; then
        log "ERROR" "Failed to restart app container."
        rollback
        exit 1
    fi

    log "INFO" "App container restarted. Waiting for health check..."
}

wait_for_health() {
    # Poll the health endpoint until it reports healthy or times out.
    local elapsed=0
    local status

    log "INFO" "Checking health at ${HEALTH_URL} (timeout: ${HEALTH_TIMEOUT}s)..."

    while [ "$elapsed" -lt "$HEALTH_TIMEOUT" ]; do
        # Attempt a health check. Suppress errors (container may still be starting).
        status=$(curl -sf "${HEALTH_URL}" 2>/dev/null || true)

        if [ -n "$status" ]; then
            # Parse the JSON response for the status field.
            local health_status
            health_status=$(echo "$status" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || true)

            if [ "$health_status" = "healthy" ]; then
                log "INFO" "Health check passed: ${status}"
                return 0
            fi

            # Got a response but not healthy.
            log "WARNING" "Health check returned: ${status}"
        fi

        sleep "$HEALTH_INTERVAL"
        elapsed=$((elapsed + HEALTH_INTERVAL))
    done

    # Timed out.
    log "ERROR" "Health check timed out after ${HEALTH_TIMEOUT}s"

    # Capture container logs for debugging.
    log "ERROR" "Last 20 lines of app container logs:"
    docker logs --tail 20 "${APP_CONTAINER}" 2>&1 | while IFS= read -r line; do
        log "ERROR" "  ${line}"
    done

    return 1
}

rollback() {
    # Restore the previous image and restart the app container.
    log "WARNING" "=== ROLLING BACK ==="

    # Check if a previous image tag exists.
    if ! docker image inspect "${APP_CONTAINER}:previous" &>/dev/null; then
        log "ERROR" "No previous image found (${APP_CONTAINER}:previous). Cannot roll back."
        log "ERROR" "Manual intervention required."
        return 1
    fi

    cd "${DEPLOY_DIR}"

    # Stop the failed container.
    log "INFO" "Stopping failed container..."
    docker compose "${COMPOSE_FILES[@]}" stop app 2>/dev/null || true

    # Re-tag the previous image as the current compose image.
    # Get the compose image name (what docker compose build targets), whole,
    # tag included, as the compose files name it: docker-compose.build.yml
    # holds the build's name (ruling plan#2703), and the ``up`` below runs
    # whatever image the host holds under it, never pulling one.
    local compose_image
    compose_image=$(docker compose "${COMPOSE_FILES[@]}" config --format json app 2>/dev/null \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['services']['app'].get('image',''))" 2>/dev/null || true)

    if [ -n "$compose_image" ]; then
        docker tag "${APP_CONTAINER}:previous" "${compose_image}"
        log "INFO" "Restored previous image as ${compose_image}"
    else
        log "WARNING" "Could not determine compose image name. Using direct container restart."
    fi

    # Restart with the previous image.
    docker compose "${COMPOSE_FILES[@]}" up -d --no-deps --force-recreate app

    # Verify the rollback succeeded.
    log "INFO" "Verifying rollback health..."
    # shellcheck disable=SC2310 # wait_for_health is a poll-with-timeout predicate returning 0 healthy / 1 timed-out; this if branches on that status and the timeout case is the handled (else) path.
    if wait_for_health; then
        log "WARNING" "Rollback successful. Previous version is running."
        log "WARNING" "Investigate the failed deployment before retrying."
    else
        log "ERROR" "Rollback FAILED. The application may be down."
        log "ERROR" "Manual intervention required. Check container logs:"
        log "ERROR" "  docker logs ${APP_CONTAINER}"
    fi
}

main() {
    # Parse command-line arguments.
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --skip-pull)
                SKIP_PULL=true
                shift
                ;;
            --skip-backup)
                SKIP_BACKUP=true
                shift
                ;;
            --skip-cosign)
                SKIP_COSIGN=true
                shift
                ;;
            --require-cosign)
                # Promote missing-cosign warnings to hard errors for this
                # run (the option this script's own guidance recommended
                # but never parsed -- OPS/SH-18).
                COSIGN_REQUIRED=true
                shift
                ;;
            --force-self-hosted)
                FORCE_SELF_HOSTED=true
                shift
                ;;
            --health-timeout)
                HEALTH_TIMEOUT="$2"
                shift 2
                ;;
            --health-interval)
                HEALTH_INTERVAL="$2"
                shift 2
                ;;
            --help)
                usage
                exit 0
                ;;
            *)
                log "ERROR" "Unknown option: $1"
                usage
                exit 1
                ;;
        esac
    done

    log "INFO" "=== Shekel Deployment Starting ==="
    log "INFO" "Deploy directory: ${DEPLOY_DIR}"

    # Canonical-stack guard (OPS/SH-14): on the maintainer host the
    # digest-pinned hardened stack lives at /opt/docker/shekel and is
    # deployed by shekel-deploy; this source-build script targets the SAME
    # compose project name and would silently swap the pinned container
    # for an unpinned local build.
    if [ -d /opt/docker/shekel ] && [ "${FORCE_SELF_HOSTED}" = false ]; then
        log "ERROR" "Canonical production stack detected at /opt/docker/shekel."
        log "ERROR" "Use the digest-pinned pipeline (shekel-deploy) to deploy there."
        log "ERROR" "If you REALLY mean to run a source-build deploy on this host,"
        log "ERROR" "rerun with --force-self-hosted."
        exit 1
    fi

    # Step 1: Check prerequisites.
    check_prerequisites

    # Step 2: Pull latest code.
    if [ "$SKIP_PULL" = false ]; then
        pull_latest
    else
        log "INFO" "Skipping git pull (--skip-pull)."
    fi

    # Step 3: Pre-deploy backup.
    if [ "$SKIP_BACKUP" = false ]; then
        backup_database
    else
        log "INFO" "Skipping pre-deploy backup (--skip-backup)."
    fi

    # Step 4: Tag current image for rollback.
    tag_previous_image

    # Step 5: Build new image.
    build_image

    # Step 6: Sign the new image with Cosign (audit C-36 / F-155).
    # The sign step runs BEFORE restart_app so a signing failure
    # aborts the deploy with the previous version still serving
    # traffic; verify_image_signature then exercises the just-
    # produced signature so a key/verifier mismatch surfaces here
    # rather than after the swap.
    sign_image

    # Step 7: Verify the signature on the image about to deploy.
    verify_image_signature

    # Step 8: Restart app container.
    restart_app

    # Step 9: Health check.
    # shellcheck disable=SC2310 # wait_for_health is a poll-with-timeout predicate returning 0 healthy / 1 timed-out; this if branches on that status and the failure case triggers rollback in the else path.
    if wait_for_health; then
        log "INFO" "=== Deployment Successful ==="
        log "INFO" "Application is healthy at ${HEALTH_URL}"

        # Clean up the rollback image to save disk space.
        docker rmi "${APP_CONTAINER}:previous" 2>/dev/null || true
    else
        log "ERROR" "=== Deployment Failed ==="
        rollback
        exit 1
    fi
}

main "$@"
