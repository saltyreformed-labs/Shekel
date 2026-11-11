#!/usr/bin/env bash
# shekel-deploy.sh -- pull, verify, and roll shekel-prod-app to a new image.
#
# Usage:
#   shekel-deploy.sh                   # pull :latest and deploy if new
#   shekel-deploy.sh sha256:abc...     # deploy that specific digest
#   shekel-deploy.sh --no-verify       # skip cosign (emergencies only)
#   shekel-deploy.sh --dry-run         # show the plan, change nothing
#   shekel-deploy.sh --dump-only       # dump the database, print the dump's path
#   shekel-deploy.sh -h | --help
#
# Every run dumps the database before it touches the pin, and refuses to
# re-pin when the previous image cannot resolve the schema revision the
# database has been left at.  The notes below say why.
#
# --dump-only takes no other argument (-h aside).  It takes the same dump a
# deploy takes, through the same function, and prints the dump's path as its
# last line.  Beyond the daemon check, the database's running-check, the
# dump and its readback it makes no call: it pulls nothing, writes no pin,
# and starts, stops or recreates no container.  The same-day production dump a release rehearsal
# restores is taken here and nowhere else (card X-cx L9, the coordinator's
# ruling under R-BAL207).

# ── Which copy of this file is actually running ────────────────────
# The canonical copy is `deploy/shekel-deploy.sh` in the Shekel repository.
# The INTENDED install (developer ruling 2026-08-08) makes
# /opt/docker/scripts/shekel-deploy.sh -- which /usr/local/bin/shekel-deploy
# symlinks -- a SYMLINK to that copy, so the two cannot drift:
#
#   sudo ln -sfn /home/josh/projects/Shekel/deploy/shekel-deploy.sh \
#                /opt/docker/scripts/shekel-deploy.sh
#
# Until that link exists the host carries a HAND-COPY, and a hand-copy is how
# finding F-14 happened: the one path that rolls production was untracked and
# unreviewed, which is why F-8 below survived unnoticed.  This script does not
# assert which arrangement is in place -- it MEASURES it at startup
# (`report_installed_copy`) and says so in its own output, because a comment
# claiming "these are the same file" is exactly the claim that goes stale.
# Since plan step balance:X-dm a working copy also needs
# scripts/_container_lib.sh at ../scripts/ beside it (ruling R-BAL254), and
# since ruling plan#3544 its own two parts, deploy/_deploy_lib.sh and
# deploy/_deploy_refusals.sh, beside it in deploy/, all of which the symlink
# has by construction: a bare hand-copy refuses to start, naming itself and
# the file it lacks, before this measurement could run.
#
# Accepted cost of the symlink, ruled: the CHECKED-OUT WORKING TREE becomes
# the live deploy path.  A half-finished edit or a feature branch is live the
# moment it is saved.  Check out the revision you mean to deploy from.
#
# ── F-8, and what this script does about it ────────────────────────
# Plan step R-F8, ruling R-R14.  Migrations run at step 3 of the deploy
# (scripts/deploy.sh), BEFORE the health check, so a failed deploy can leave
# the database stamped at a revision the other image's Alembic tree does not
# contain.  That image's deploy then dies at step 3 too, and re-pinning it
# turns one dead container into two -- reproduced on revision d4a71f6e30bb as
# `CommandError: Can't locate revision`.
#
# Since card plan#2166 the deploy runs in the MIGRATE service, a one-shot from
# the app's image that `docker compose up -d app` runs, and waits on, before it
# starts the app: a deploy that fails fails the `up`, leaving the app's new
# container created and not started.  The app's own entrypoint runs no
# migration.  So "step 3" below is the migrate container's, and its log is the
# one that says why a deploy failed.
#
# THE QUESTION IS NOT "does this release add migrations".  That is directional,
# and it gets the rollback case backwards: deploying an OLDER image adds
# nothing while being exactly the image that cannot resolve today's schema.
# The question is "can image X resolve the revision the database is stamped
# at", asked of the real `alembic_version` row.  So:
#
#   * before anything is written, the TARGET must be able to resolve the
#     revision the database is stamped at now -- otherwise it would die at
#     step 3 and the deploy is refused up front;
#   * every deploy takes a pg_dump -Fc first; no dump, no deploy;
#   * after a failure the stamp is RE-READ, and the pin is reverted only if
#     the previous image can resolve what the database now says.  Otherwise
#     the script names the dump and stops.  A release that migrated nothing
#     leaves the stamp untouched, so it still rolls back automatically -- 6 of
#     the last 10 releases were pure digest reverts.
#
# Since plan step balance:X-cv, the deploy's step 3 is ONE transaction: the
# migrations, the reference seed, the three deploy hooks and the audit-trigger
# check in `scripts/init_database.py` commit together or not at all.  So a
# failure INSIDE step 3 -- a migration that errors, a hook that refuses, a
# missing audit trigger -- leaves the stamp where it was, and this
# script re-pins the previous image even for a migration-bearing release.
# Only a failure AFTER step 3 has committed (the deploy's step 4, the first
# user's seed, or the health check) leaves the new stamp, and that is the
# case the refusal still meets.  A re-pin is not a recovery when the fault is in something both
# images meet: a missing audit trigger is counted by every image since C-13,
# so the previous image refuses the same database and this script ends
# "rollback container also unhealthy" (docs/runbook/2_deployment.md s.2.3 names the repair).
# The re-read stamp is still the only question; since X-cv it is read only
# once nothing that could still commit is running -- since card plan#2166 the
# migrate container, where step 3 runs, is STOPPED if it is -- and FOR SHARE,
# so a step 3 still in flight cannot commit behind the read
# (`stop_failed_container`, `db_stamped_revisions`).  The failed new app is
# stopped too, so a failed release is never left serving, but it cannot move
# the stamp, so a stop of it that fails only warns (ruling plan#3539).  Before
# re-pinning, the failed deploy's and app's logs are saved beside the dump,
# because re-pinning recreates both containers and `docker logs` then shows
# the previous image instead (`save_failed_container_log`).
#
# A re-pin is a recovery only when the previous image can run under the
# compose files in place.  The first release of card plan#2166 is the case it
# cannot: an image built before it has no scripts/owner_entrypoint.sh, which
# the migrate service runs.  The pre-flight asks, and a failure then refuses
# rather than re-pins (`image_runs_this_layout`, `refuse_layout`).
#
# Shekel's prod image is pinned in /opt/docker/shekel/.env via
# SHEKEL_IMAGE_DIGEST (the compose override consumes it as
# `ghcr.io/saltyreformed-labs/shekel@${SHEKEL_IMAGE_DIGEST:?...}`).
# bump-digest.sh can't touch this because it rejects env-var-templated
# images on purpose -- this script is the canonical path for shekel.

set -euo pipefail

# The file running, resolved once: /usr/local/bin/shekel-deploy reaches the
# repository through symlinks.  `usage` reads its header from it and
# `report_installed_copy` reports it.
_self=$(readlink -f "${BASH_SOURCE[0]}")

# A file this script loads from its checkout, missing or unreadable, stops it
# here, before it touches anything, naming this copy and that file: the
# startup report below has not run, so this is the one line that says which
# copy stopped.
cannot_load() {
    printf '[shekel-deploy] ERROR: %s cannot load %s,\n   %s\n' "$_self" "$1" "$2" >&2
    exit 1
}

# "Is this container running?" has one home in the shell scripts, shared
# with the backup scripts (plan step balance:X-dm, ruling R-BAL254), loaded
# from THIS script's checkout, where scripts/_container_lib.sh sits beside
# deploy/.  First, so a copy without it refuses to start, before it touches
# anything (the ruling's words) -- and names itself, since the startup report
# below does not run.
_container_lib="${_self%/*}/../scripts/_container_lib.sh"
# shellcheck source=../scripts/_container_lib.sh
source "$_container_lib" || cannot_load "$_container_lib" "the running-check it reads from its own checkout.  Run the repository copy
   (see its header): a copy needs scripts/_container_lib.sh at ../scripts/."

# This script's own functions, in the two files beside it (ruling plan#3544:
# no file over 1,000 lines), loaded from its checkout as the running-check
# is, so a copy without one refuses the same way.
_part_needed="a part of this script.  Run the repository copy (see its header): a
   copy needs deploy/_deploy_lib.sh and deploy/_deploy_refusals.sh beside it."
_deploy_lib="${_self%/*}/_deploy_lib.sh"
# shellcheck source=_deploy_lib.sh
source "$_deploy_lib" || cannot_load "$_deploy_lib" "$_part_needed"
_deploy_refusals="${_self%/*}/_deploy_refusals.sh"
# shellcheck source=_deploy_refusals.sh
source "$_deploy_refusals" || cannot_load "$_deploy_refusals" "$_part_needed"

# ── Configuration ──────────────────────────────────────────────────
# Every value is overridable from the environment, defaulting to production.
# That is not configurability for its own sake: R-F8's negative control has to
# drive this script end to end against a THROWAWAY stack and a restored clone
# -- planting a failing health check on a migration-bearing deploy and
# watching it refuse -- and a script whose targets are literals can only be
# tested by reading it.
SHEKEL_DIR="${SHEKEL_DIR:-/opt/docker/shekel}"
ENV_FILE="${SHEKEL_ENV_FILE:-${SHEKEL_DIR}/.env}"
COMPOSE_SERVICE="${SHEKEL_COMPOSE_SERVICE:-app}"
CONTAINER_NAME="${SHEKEL_CONTAINER_NAME:-shekel-prod-app}"
# The migrate service's container (its container_name in docker-compose.yml),
# where the deploy runs, and the entrypoint that service runs, read from the
# image by the pre-flight.
MIGRATE_CONTAINER="${SHEKEL_MIGRATE_CONTAINER:-shekel-prod-migrate}"
MIGRATE_ENTRYPOINT="${SHEKEL_MIGRATE_ENTRYPOINT:-/home/shekel/app/scripts/owner_entrypoint.sh}"
IMAGE_REPO="${SHEKEL_IMAGE_REPO:-ghcr.io/saltyreformed-labs/shekel}"
MOVING_TAG="${SHEKEL_MOVING_TAG:-latest}"

# Pre-deploy dump target.  The directory and naming match the predeploy dumps
# the operator has been taking by hand before each migration-bearing release
# (~/shekel-backups/shekel_prod_predeploy_<label>_<ts>.dump); this automates
# that habit rather than inventing a second convention beside it.  A
# --dump-only dump keeps the directory and changes the label, as
# shekel_prod_dump_<current pin>_<ts>.dump, so it is never mistaken for the
# restore point a deploy took.
DB_CONTAINER="${SHEKEL_DB_CONTAINER:-shekel-prod-db}"
DB_USER="${SHEKEL_DB_USER:-shekel_user}"
DB_NAME="${SHEKEL_DB_NAME:-shekel}"
BACKUP_DIR="${SHEKEL_BACKUP_DIR:-${HOME}/shekel-backups}"

# Path to the migrations directory INSIDE the image, used by the pre-flight.
IMAGE_MIGRATIONS_DIR="${SHEKEL_IMAGE_MIGRATIONS_DIR:-migrations/versions}"

# cosign keyless verification -- must match the workflow that signed the image.
# ONE identity: the image-build workflow of saltyreformed-labs/Shekel, on main.
# Anchored at both ends and case exact, because cosign matches the regexp
# anywhere in the certificate's SAN: the unanchored form this replaced accepted
# a build of ANY ref.  No alternation, so a build signed before the move to
# saltyreformed-labs (ruling R-BAL173) is refused too, as is an explicit
# `shekel-deploy sha256:<pre-move digest>`.  The automatic rollback re-pins
# without verifying, so this check never refuses it.
COSIGN_IDENTITY_REGEXP='^https://github\.com/saltyreformed-labs/Shekel/\.github/workflows/docker-publish\.yml@refs/heads/main$'
COSIGN_OIDC_ISSUER="https://token.actions.githubusercontent.com"

# ntfy.  Overridable for the same reason as the block above -- a rehearsal
# that paged the operator with a fake "deploy FAILED" would be worse than no
# rehearsal.  Point NTFY_TOKEN_FILE at a nonexistent path to silence it.
NTFY_URL="${SHEKEL_NTFY_URL:-https://ntfy.saltyreformed.com}"
NTFY_TOPIC="${SHEKEL_NTFY_TOPIC:-alerts}"
NTFY_TOKEN_FILE="${SHEKEL_NTFY_TOKEN_FILE:-/opt/docker/monitoring/secrets/wud_ntfy_token}"

# Health poll
HEALTH_TIMEOUT_S="${SHEKEL_HEALTH_TIMEOUT_S:-240}"
HEALTH_INTERVAL_S="${SHEKEL_HEALTH_INTERVAL_S:-5}"

# ── Arg parsing ────────────────────────────────────────────────────
usage() {
    sed -n '2,/^$/p' "$_self" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

verify=true
dryrun=false
dumponly=false
explicit_digest=""
for a in "$@"; do
    case "$a" in
        -h | --help) usage 0 ;;
        --no-verify) verify=false ;;
        --dry-run) dryrun=true ;;
        --dump-only) dumponly=true ;;
        sha256:*)
            if [ -n "$explicit_digest" ]; then
                echo "ERROR: more than one digest argument given." >&2
                exit 2
            fi
            explicit_digest="$a"
            ;;
        *)
            echo "ERROR: unknown argument: $a" >&2
            usage 2
            ;;
    esac
done
# A dump taken beside a digest, --dry-run or --no-verify would read as a
# deploy, a plan or a skipped check that never happened.
if $dumponly && [ "$#" -ne 1 ]; then
    echo "ERROR: --dump-only takes no other argument." >&2
    exit 2
fi

# ── Pre-flight ─────────────────────────────────────────────────────
# Globals the helpers above set and read.  Declared here so `set -u` cannot be
# tripped by a path that reaches a failure branch before the pre-flight ran.
OLD_MIGRATIONS=""
NEW_MIGRATIONS=""
ADDED_REVISIONS=""
STAMPED_REVISIONS=""
DUMP_PATH=""
DUMP_PART=""
FAILED_LOG_PATH=""
# The pre-flight's answer; false until it asks, so a path that reached a
# failure without it would refuse rather than re-pin.
OLD_RUNS_HERE=false

# shellcheck disable=SC2329 ## invoked indirectly, by the `trap ... EXIT`
# immediately below; shellcheck does not follow trap handlers.
sweep_part_file() {
    # OPS/SH-04's third part: a Ctrl-C mid-dump must not leave a straggler.
    # if-form, not `[ ] &&` -- errexit is ACTIVE inside an EXIT trap, so a
    # falsy last command would override the script's real exit status.
    if [ -n "$DUMP_PART" ]; then
        rm -f "$DUMP_PART"
    fi
    return 0
}
trap sweep_part_file EXIT

report_installed_copy

# Captured once rather than substituted inside each message: a command
# substitution inside a string masks the inner command's status (SC2312), and
# the value is the same for the whole run.
whoami_name=$(id -un)
[ -r "$ENV_FILE" ] || die "$ENV_FILE not readable."
# Only a run that writes the pin needs it writable; a dump-only run reads it.
$dumponly || [ -w "$ENV_FILE" ] || die "$ENV_FILE not writable by ${whoami_name}."
docker info >/dev/null 2>&1 || die "docker daemon unreachable for ${whoami_name}."

old_digest=$(read_env_digest)
[ -n "$old_digest" ] || die "no SHEKEL_IMAGE_DIGEST in $ENV_FILE."
# The whole digest, not its prefix: a dump-only run names its file for it
# and makes no later call that would refuse a malformed one.
[[ "$old_digest" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || die "current SHEKEL_IMAGE_DIGEST is not sha256:<64 hex digits> (got '$old_digest')."
# Captured once rather than substituted inside each message, for the same
# reason as ``whoami_name`` above.
old_short=$(short "$old_digest")

# ── Dump only ──────────────────────────────────────────────────────
# Before the pull: a dump-only run reads the pin and the database, nothing
# else.  No pre-flight either, since listing an image's migrations starts a
# container.
if $dumponly; then
    take_dump "dump_${old_short}"
    printf '%s\n' "$DUMP_PATH"
    exit 0
fi

# ── Resolve target digest ──────────────────────────────────────────
if [ -n "$explicit_digest" ]; then
    new_digest="$explicit_digest"
    log "explicit target: $new_digest"
    if ! docker pull -q "${IMAGE_REPO}@${new_digest}" >/dev/null; then
        die "docker pull ${IMAGE_REPO}@${new_digest} failed."
    fi
else
    log "pulling ${IMAGE_REPO}:${MOVING_TAG} ..."
    if ! docker pull -q "${IMAGE_REPO}:${MOVING_TAG}" >/dev/null; then
        die "docker pull ${IMAGE_REPO}:${MOVING_TAG} failed."
    fi
    # Resolve the just-pulled tag's index/manifest digest: the first digest
    # among its RepoDigests.  Captured, then matched in bash, with no pipe
    # (plan step balance:X-dm).  `| grep -oE ... | head -1` under pipefail
    # could kill grep once head had its line, and set -e then ended the run
    # with no message of its own -- as it did for a failed inspect, and for
    # an answer holding no digest, whose refusal below never ran.
    if ! repo_digests=$(docker image inspect "${IMAGE_REPO}:${MOVING_TAG}" \
        --format '{{range .RepoDigests}}{{println .}}{{end}}'); then
        die "docker image inspect ${IMAGE_REPO}:${MOVING_TAG} failed."
    fi
    [[ "$repo_digests" =~ sha256:[a-f0-9]{64} ]] \
        || die "could not resolve digest for ${IMAGE_REPO}:${MOVING_TAG}."
    new_digest=${BASH_REMATCH[0]}
fi

# Captured once, as ``old_short`` is above.
new_short=$(short "$new_digest")

log "current pin: $old_digest"
log "target pin:  $new_digest"

if [ "$old_digest" = "$new_digest" ]; then
    # "Nothing to do" is a claim about the PIN.  After a refused rollback the
    # pin deliberately equals the target while the app is down, and re-running
    # this script is the operator's most natural next move -- so exiting 0
    # here without looking at the container reports success over a dead stack.
    health=$(docker inspect "$CONTAINER_NAME" \
        --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
        2>/dev/null || echo missing)
    if [ "$health" = "healthy" ] || [ "$health" = "none" ]; then
        log "already at target digest; nothing to do."
        exit 0
    fi
    log "already at target digest, but $CONTAINER_NAME is '${health}'."
    log "  The pin is not the problem, so re-running a deploy will not fix it."
    log "  If a previous run REFUSED a rollback, the database still needs"
    log "  restoring -- that run named the dump and the exact sequence."
    log "    docker logs ${CONTAINER_NAME}"
    ntfy_notify 4 "Shekel deploy no-op, container ${health}" \
        "Pin already at ${new_short} but ${CONTAINER_NAME} is ${health}. Deploying cannot help; check whether a refused rollback is still outstanding."
    exit 1
fi

# ── cosign verify ──────────────────────────────────────────────────
if $verify; then
    if ! command -v cosign >/dev/null 2>&1; then
        cat >&2 <<EOF
[shekel-deploy] ERROR: cosign is not installed.
  Your prod image is keyless-signed by .github/workflows/docker-publish.yml
  and your audit posture (F-155) requires verification before deploy.

  Install (one-shot, current stable):
    curl -sSL -o /tmp/cosign \\
      https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-amd64
    sudo install -m 0755 /tmp/cosign /usr/local/bin/cosign
    cosign version

  Then re-run this script.
  Override (NOT recommended -- bypasses the supply-chain check):
    $0 --no-verify $*
EOF
        exit 1
    fi
    log "cosign verify ${IMAGE_REPO}@${new_digest} ..."
    if ! cosign verify \
        --certificate-identity-regexp "$COSIGN_IDENTITY_REGEXP" \
        --certificate-oidc-issuer "$COSIGN_OIDC_ISSUER" \
        "${IMAGE_REPO}@${new_digest}" >/dev/null 2>&1; then
        die "cosign verify FAILED for ${IMAGE_REPO}@${new_digest}. Aborting."
    fi
    log "cosign verify OK."
else
    log "WARN: cosign verify skipped (--no-verify)."
fi

# ── Migration pre-flight ───────────────────────────────────────────
# Before anything is written.  Refuses a target that could not boot against
# this database, and says whether a failure would be recoverable by
# re-pinning -- the dry-run reports it, so that is answerable without
# deploying.
preflight_migrations "$old_digest" "$new_digest"

if [ -n "$ADDED_REVISIONS" ]; then
    log "MIGRATION-BEARING release: the target adds these revisions --"
    printf '%s\n' "$ADDED_REVISIONS" | sed 's/^/    /'
    log "  The deploy's step 3, in the migrate service, runs them, the seeds,"
    log "  the deploy hooks and the audit-trigger check in ONE transaction, so"
    log "  a failure there leaves the stamp where ${old_short} can resolve it."
    log "  If step 3 COMMITS and a later step or the health check then fails,"
    log "  re-pinning ${old_short} will NOT work.  This script re-reads the stamp"
    log "  after a failure and refuses rather than making a second dead container."
else
    log "the target adds no migrations, so a failure leaves the schema where"
    log "${old_short} can still resolve it."
fi
if $OLD_RUNS_HERE; then
    log "${old_short} runs under the compose files in place, so the rollback"
    log "applies as usual."
else
    log "${old_short} CANNOT run under the compose files in place: their migrate"
    log "  service runs ${MIGRATE_ENTRYPOINT}, which ${old_short} does not hold."
    log "  A failure is NOT rolled back by re-pinning it: this script refuses,"
    log "  leaves the pin at ${new_short} and names the way back, which takes the"
    log "  compose files ${old_short} ran under as well as its digest."
fi

# ── Dry-run exit ───────────────────────────────────────────────────
if $dryrun; then
    log "dry-run: would dump the database, rewrite .env, and recreate $CONTAINER_NAME."
    log "         $old_digest"
    log "      -> $new_digest"
    exit 0
fi

# ── Pre-deploy dump ────────────────────────────────────────────────
# Unconditional, and BEFORE the pin is rewritten: no dump, no deploy.  It is
# taken for every release, not only migration-bearing ones, because "this
# release could not have moved the schema" is a claim about the image, and
# the dump costs about half a megabyte.
take_dump "predeploy_${new_short}"

# ── Deploy ─────────────────────────────────────────────────────────
log "rewriting SHEKEL_IMAGE_DIGEST in $ENV_FILE ..."
write_env_digest "$new_digest"

log "docker compose up -d $COMPOSE_SERVICE ..."
# shellcheck disable=SC2310 ## deliberate: a boolean predicate, and its
# own failure is the branch being taken -- not an error to propagate.
if ! compose_up; then
    # `docker compose up -d` reporting failure USUALLY means the deploy
    # failed in the migrate service, which the app waits on, and then the
    # app's new container was created and never started.  A failure inside
    # the deploy's step 3 migrated nothing; one after it (step 4, the first
    # user's seed) leaves the new stamp; and the `up` can fail with the
    # deploy still running.  Rather than guess from the exit status, ask the
    # database: if the stamp is still something ${old_short} can resolve, the
    # ordinary revert is correct.  Asked once a container that could still
    # commit is stopped, so the answer is final.
    # shellcheck disable=SC2310 ## boolean predicate, failure is the branch.
    if ! stop_failed_container; then
        refuse_unstoppable "docker compose up rejected ${new_short}."
    fi
    if ! $OLD_RUNS_HERE; then
        refuse_layout "docker compose up rejected ${new_short}."
    fi
    # shellcheck disable=SC2310 ## boolean predicate, failure is the branch.
    if ! repin_is_safe; then
        refuse_to_repin "docker compose up rejected ${new_short}."
    fi
    log "compose up failed; reverting .env to $old_digest."
    save_failed_container_log
    write_env_digest "$old_digest"
    # shellcheck disable=SC2310 ## deliberate: the WARN is the handling.
    compose_up || log "WARN: revert compose up also failed; container state unknown."
    ntfy_notify 5 "Shekel deploy FAILED (compose)" \
        "compose up rejected ${new_short}; reverted to ${old_short}. Failed containers' logs: ${FAILED_LOG_PATH:-in Loki only}"
    exit 1
fi

log "waiting for $CONTAINER_NAME to report healthy (timeout ${HEALTH_TIMEOUT_S}s) ..."
# shellcheck disable=SC2310 ## deliberate: a boolean predicate, and its
# own failure is the branch being taken -- not an error to propagate.
if wait_healthy; then
    log "deploy OK: ${old_short} -> ${new_short}"
    log "pre-deploy dump kept at: ${DUMP_PATH}"
    ntfy_notify 3 "Shekel deployed" \
        "${old_short} -> ${new_short}"
    exit 0
fi

# ── Rollback ───────────────────────────────────────────────────────
# The F-8 fork.  A stamp the previous image cannot resolve -- a release whose
# deploy's step 3 COMMITTED its migrations before something later failed --
# has no working rollback, so it gets the refusal instead of a second dead
# container.  A step-3 failure left the stamp unmoved (plan step X-cv) and
# re-pins below.  A migrate container still running the deploy is stopped
# before the stamp is read, and the read waits for any commit it already sent,
# so a step 3 still in flight cannot commit behind the read (ruling R-BAL115).
# shellcheck disable=SC2310 ## boolean predicate, failure is the branch.
if ! stop_failed_container; then
    refuse_unstoppable "${new_short} did not report healthy within ${HEALTH_TIMEOUT_S}s."
fi
if ! $OLD_RUNS_HERE; then
    refuse_layout "${new_short} did not report healthy within ${HEALTH_TIMEOUT_S}s."
fi
# shellcheck disable=SC2310 ## boolean predicate, failure is the branch.
if ! repin_is_safe; then
    refuse_to_repin "${new_short} did not report healthy within ${HEALTH_TIMEOUT_S}s."
fi

log "rolling back to $old_digest ..."
save_failed_container_log
write_env_digest "$old_digest"
# shellcheck disable=SC2310 ## deliberate: a boolean predicate, and its
# own failure is the branch being taken -- not an error to propagate.
if compose_up && wait_healthy; then
    log "rollback healthy. Failed digest: $new_digest"
    log "pre-deploy dump kept at: ${DUMP_PATH}"
    log "failed containers' logs: ${FAILED_LOG_PATH:-in Loki only}"
    ntfy_notify 5 "Shekel deploy FAILED, rolled back" \
        "${new_short} did not become healthy. Reverted to ${old_short}. Failed containers' logs: ${FAILED_LOG_PATH:-in Loki only}"
    exit 1
fi

# Rollback also unhealthy -- manual intervention required.  Reached only when
# the previous image CAN resolve the current stamp, so the migration level is
# not the suspect; something both images meet is (schema drift the stamp does
# not record, the data, or the environment).  The dump is the recovery only
# when the failed release's step 3 committed what the previous image now
# refuses; docs/runbook/2_deployment.md s.2.3.  Also reached when the re-pin's own
# `compose up` failed and usually nothing started (finding BAL-540).
log "pre-deploy dump kept at: ${DUMP_PATH}"
log "failed containers' logs: ${FAILED_LOG_PATH:-in Loki only}"
ntfy_notify 5 "Shekel deploy FAILED, rollback UNHEALTHY" \
    "Neither ${new_short} nor rollback ${old_short} is healthy. Container needs manual attention. Dump: ${DUMP_PATH}. Failed containers' logs: ${FAILED_LOG_PATH:-in Loki only}"
die "rollback container also unhealthy; manual intervention required."
