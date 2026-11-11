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
# scripts/_container_lib.sh at ../scripts/ beside it (ruling R-BAL254), which
# the symlink has by construction: a bare hand-copy refuses to start, naming
# itself and the file it lacks, before this measurement could run.
#
# Accepted cost of the symlink, ruled: the CHECKED-OUT WORKING TREE becomes
# the live deploy path.  A half-finished edit or a feature branch is live the
# moment it is saved.  Check out the revision you mean to deploy from.
#
# ── F-8, and what this script does about it ────────────────────────
# Plan step R-F8, ruling R-R14.  Migrations run at entrypoint step 3, BEFORE
# the health check, so a failed deploy can leave the database stamped at a
# revision the other image's Alembic tree does not contain.  That image then
# dies at step 3 too, and re-pinning it turns one dead container into two --
# reproduced on revision d4a71f6e30bb as `CommandError: Can't locate revision`.
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
# Since plan step balance:X-cv, entrypoint step 3 is ONE transaction: the
# migrations, the reference seed and the audit-trigger check in
# `scripts/init_database.py` commit together or not at all (the ledger hooks
# did too, until plan task plan#3077).  So a failure INSIDE step 3 -- a failed
# migration or seed, a missing audit trigger -- leaves the stamp unmoved, and
# this script re-pins the previous image even for a migration-bearing release.
# Only a failure AFTER step 3 has committed (a later entrypoint step, or the
# health check) leaves the new stamp, and that is the case the refusal still
# meets.  A re-pin is not a recovery when the fault is in something both
# images meet: a missing audit trigger is counted by every image since C-13,
# so the previous image refuses the same database and this script ends
# "rollback container also unhealthy" (docs/runbook.md s.2.3 names the repair).
# The re-read stamp is still the only question; since X-cv it is read only
# after the target image's container has been STOPPED, and FOR SHARE, so a
# step 3 still in flight cannot commit behind the read
# (`stop_failed_container`, `db_stamped_revisions`).  Before
# re-pinning, the failed container's log is saved beside the dump, because
# re-pinning recreates the container and `docker logs` then shows the previous
# image instead (`save_failed_container_log`).
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

# "Is this container running?" has one home in the shell scripts, shared
# with the backup scripts (plan step balance:X-dm, ruling R-BAL254), loaded
# from THIS script's checkout, where scripts/_container_lib.sh sits beside
# deploy/.  First, so a copy without it refuses to start, before it touches
# anything (the ruling's words) -- and names itself, since the startup report
# below does not run.
_container_lib="${_self%/*}/../scripts/_container_lib.sh"
# shellcheck source=../scripts/_container_lib.sh
source "$_container_lib" || {
    printf '[shekel-deploy] ERROR: %s cannot load %s,\n   the running-check it reads from its own checkout.  Run the repository copy\n   (see its header): a copy needs scripts/_container_lib.sh at ../scripts/.\n' \
        "$_self" "$_container_lib" >&2
    exit 1
}

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

# ── Helpers ────────────────────────────────────────────────────────
log() { printf '[shekel-deploy] %s\n' "$*"; }
die() {
    printf '[shekel-deploy] ERROR: %s\n' "$*" >&2
    exit 1
}

ntfy_notify() {
    # ntfy_notify <priority 1-5> <title> <body>
    local prio="$1" title="$2" body="$3"
    if [ ! -r "$NTFY_TOKEN_FILE" ]; then
        log "WARN: $NTFY_TOKEN_FILE unreadable; skipping ntfy."
        return 0
    fi
    local token
    token=$(tr -d '\r\n' <"$NTFY_TOKEN_FILE")
    curl -fsS --max-time 10 \
        -H "Authorization: Bearer ${token}" \
        -H "Title: ${title}" \
        -H "Priority: ${prio}" \
        -H "Tags: shekel,deploy" \
        -d "${body}" \
        "${NTFY_URL}/${NTFY_TOPIC}" >/dev/null \
        || log "WARN: ntfy publish failed (non-fatal)."
}

short() { printf '%s' "${1#sha256:}" | cut -c1-12; }

read_env_digest() {
    awk -F= '/^SHEKEL_IMAGE_DIGEST=/{sub(/^SHEKEL_IMAGE_DIGEST=/,""); print; exit}' "$ENV_FILE"
}

write_env_digest() {
    # In-place rewrite preserving file perms (.env is mode 600 josh:josh).
    local new="$1"
    local tmp
    tmp=$(mktemp "${ENV_FILE}.XXXXXX")
    trap 'rm -f "$tmp"' RETURN
    # POSIX sed with a quoted | delimiter is safe -- sha256:... has no |.
    sed "s|^SHEKEL_IMAGE_DIGEST=.*|SHEKEL_IMAGE_DIGEST=${new}|" "$ENV_FILE" >"$tmp"
    if ! grep -q "^SHEKEL_IMAGE_DIGEST=${new}$" "$tmp"; then
        rm -f "$tmp"
        die "failed to rewrite SHEKEL_IMAGE_DIGEST in $ENV_FILE (line missing?)"
    fi
    chmod --reference="$ENV_FILE" "$tmp"
    mv "$tmp" "$ENV_FILE"
    trap - RETURN
}

report_installed_copy() {
    # Say which file is executing and whether it is the repository's copy.
    # MEASURED rather than asserted: this header previously claimed the host
    # path was a symlink to the repo, and a stale hand-copy made that claim
    # false while reading as reassurance.  Printing the answer on every run is
    # what keeps it honest.
    local running="$_self" canonical canonical_real
    log "running: ${running}"
    canonical="${SHEKEL_CANONICAL_COPY:-/home/josh/projects/Shekel/deploy/shekel-deploy.sh}"
    [ -r "$canonical" ] || return 0
    canonical_real=$(readlink -f "$canonical")
    if [ "$running" = "$canonical_real" ]; then
        return 0
    fi
    log "WARN: this is NOT the repository's copy (${canonical})."
    if diff -q "$running" "$canonical" >/dev/null 2>&1; then
        log "      Contents match today, but nothing keeps them in step."
    else
        log "      Contents DIFFER -- the reviewed script is not the one about"
        log "      to roll production.  Compare them before continuing:"
        log "        diff '${running}' '${canonical}'"
    fi
    log "      Install the symlink: sudo ln -sfn '${canonical}' \\"
    log "        /opt/docker/scripts/shekel-deploy.sh"
}

image_migration_revisions() {
    # Echo the migration filenames an image carries, one per line, C-sorted.
    #
    # Runs the image with its entrypoint replaced, so nothing boots: no
    # database connection, no migration, no server.  The listing is NOT
    # error-suppressed: a missing directory -- a `migrations/` rename, a
    # changed WORKDIR -- must surface as a failure, because an empty list is
    # otherwise indistinguishable from "this image has no migrations" and
    # would silently disable every check built on it.
    local digest="$1"
    docker run --rm --entrypoint /bin/sh "${IMAGE_REPO}@${digest}" -c \
        "ls -1 ${IMAGE_MIGRATIONS_DIR}/*.py | xargs -r -n1 basename | LC_ALL=C sort"
}

image_listing_or_die() {
    # Echo one image's migration listing, refusing an empty or failed answer.
    local digest="$1" role="$2" listing
    # shellcheck disable=SC2310 ## deliberate, and the shape used throughout
    # this script: the helper is one `docker run` whose status IS the branch
    # condition, so errexit being disabled inside it masks nothing.
    if ! listing=$(image_migration_revisions "$digest"); then
        die "could not list ${IMAGE_MIGRATIONS_DIR} in the ${role} image
   (${digest}).  Refusing to deploy: every check below rests on that listing,
   and guessing would re-arm the failure this script exists to stop."
    fi
    if [ -z "$listing" ]; then
        die "the ${role} image (${digest}) reports ZERO migrations in
   ${IMAGE_MIGRATIONS_DIR}.  Every real image carries over a hundred, so this
   is a broken probe -- a renamed directory or a changed WORKDIR -- not an
   image without migrations.  Refusing to read it as 'nothing to worry about',
   which is how this check would silently stop working."
    fi
    printf '%s\n' "$listing"
}

db_stamped_revisions() {
    # Echo the Alembic revision(s) the database is currently stamped at.
    # This row is the authority: `CommandError: Can't locate revision` names
    # this value, so "can image X boot against this database" is decidable
    # from it plus X's migration listing.
    #
    # FOR SHARE, so the read waits for a transaction that holds the row (plan
    # step balance:X-cv, ruling R-BAL115).  Alembic stamps with an UPDATE, so
    # a step 3 that migrated holds the row lock until it ends; a COMMIT its
    # container sent before being stopped still completes on the server, and
    # this read then returns what it committed rather than the old value.  A
    # step 3 cut off mid-statement aborts at its next read from the dead
    # client, so the wait is one statement at most -- a count, not a time:
    # nothing bounds that statement (statement_timeout is 0), so a step 3
    # stuck behind another session's lock holds this read as long.  The
    # re-read logs before it waits (`repin_is_safe`).  The pre-flight's read,
    # with nothing migrating, never waits.
    docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc \
        "SELECT version_num FROM public.alembic_version FOR SHARE"
}

image_resolves_revisions() {
    # 0 when *listing* carries a migration script for every revision given.
    # Alembic resolves a revision by finding the script whose id it is, so
    # file presence answers the same question without booting anything.
    #
    # A `case` over the listing, with NO PIPE (plan step balance:X-dm, finding
    # BAL-618).  The spelling it replaced, `printf '%s\n' "$listing" | grep -q
    # "^${rev}_"`, could answer NO for a listed revision: printf writes a
    # multi-line listing in more than one write(2), `grep -q` exits at its
    # first match, and a match on an early line could leave printf writing into
    # a closed pipe.  SIGPIPE killed printf with status 141, and pipefail then
    # failed the pipeline.  Every caller asks a safety question (the pre-flight,
    # the re-pin decision, the refusal's diagnosis), so a false NO refused a
    # deploy, refused a rollback that would have worked, or named only the dump
    # restore.  The leading newline lets the first line match like every other
    # line, and the quoted revision matches literally, where grep read it as a
    # pattern (every revision id is lowercase letters and digits).
    local listing="$1" revisions="$2" rev
    for rev in $revisions; do
        case $'\n'"${listing}" in
            *$'\n'"${rev}"_*) : ;;
            *) return 1 ;;
        esac
    done
    return 0
}

preflight_migrations() {
    # Establish OLD_MIGRATIONS, NEW_MIGRATIONS, STAMPED_REVISIONS and the
    # informational ADDED_REVISIONS, then refuse a target that could not boot
    # against the database as it stands.
    #
    # "Does this release ADD migrations" is NOT the safety question -- it is
    # directional, and it reads a rollback (an older target, which adds
    # nothing) as safe when it is precisely the image that cannot resolve
    # today's schema.  The question asked here is direction-free.
    local old="$1" new="$2"
    log "pre-flight: reading ${IMAGE_MIGRATIONS_DIR} from both images ..."
    OLD_MIGRATIONS=$(image_listing_or_die "$old" "CURRENT")
    NEW_MIGRATIONS=$(image_listing_or_die "$new" "TARGET")

    # LC_ALL=C on comm: the listings were sorted in the container's C locale,
    # and comm verifies its inputs' order in the CALLER's.  Today's filenames
    # collate identically either way, but not every revision id here is hex
    # (c3d4e5f6g7h8_... exists), so that agreement is not structural.
    ADDED_REVISIONS=$(LC_ALL=C comm -13 \
        <(printf '%s\n' "$OLD_MIGRATIONS") \
        <(printf '%s\n' "$NEW_MIGRATIONS"))

    # shellcheck disable=SC2310 ## boolean predicate; see image_listing_or_die.
    if ! STAMPED_REVISIONS=$(db_stamped_revisions); then
        die "could not read public.alembic_version from ${DB_CONTAINER}.  That
   row decides whether a rollback is possible, so this script will not proceed
   without it."
    fi
    if [ -z "$STAMPED_REVISIONS" ]; then
        die "public.alembic_version is EMPTY on ${DB_NAME}.  An uninitialised
   database is not a state this script should deploy over."
    fi
    local stamped_line
    stamped_line=$(printf '%s' "$STAMPED_REVISIONS" | tr '\n' ' ')
    log "database is stamped at: ${stamped_line}"

    # shellcheck disable=SC2310 ## boolean predicate, failure is the branch.
    if ! image_resolves_revisions "$NEW_MIGRATIONS" "$STAMPED_REVISIONS"; then
        die "the TARGET image cannot resolve the revision this database is
   stamped at, so it would die at entrypoint step 3 exactly as the previous
   image does in finding F-8.  This is what deploying an image OLDER than the
   database looks like.  A genuine downgrade means restoring a dump taken at
   or before that revision FIRST, then deploying this digest."
    fi
}

stop_failed_container() {
    # Make the stamp read that follows FINAL (plan step balance:X-cv, rulings
    # R-BAL115 and R-BAL119).  A container running the TARGET image may still
    # be inside entrypoint step 3, whose one transaction could commit the new
    # stamp after it is read, so that container is stopped first: once stopped
    # it can start no NEW commit, and a COMMIT it had already sent is what the
    # read's FOR SHARE waits for (`db_stamped_revisions`).  It runs
    # `restart: unless-stopped`, so a manual stop holds.
    #
    # Only the target image's container, told apart by IMAGE ID, not by the
    # reference spelling the compose file happens to use.  An absent container
    # moves nothing, and on the compose-failure path compose can fail BEFORE
    # replacing the running previous container, which cannot move the stamp and
    # is still serving: stopping either would buy no finality, only downtime.
    # A target whose ID cannot be read is no reason to skip the stop, so the
    # container is stopped then: downtime is the cheaper error.
    #
    # Returns 1 when a target-image container could not be stopped.  That is
    # the one case whose read is not final, and the caller refuses to re-pin
    # on it.  A container whose image cannot be read counts as absent: if the
    # daemon is down, the stamp read after this fails too, which already
    # refuses.  Stated, not closed: a failure of this one inspect that the
    # daemon recovers from before the read skips the stop, and that read is
    # then as final as every read was before X-cv.
    local running target
    running=$(docker inspect --format '{{.Image}}' "$CONTAINER_NAME" 2>/dev/null || true)
    if [ -z "$running" ]; then
        log "no ${CONTAINER_NAME} container to stop."
        return 0
    fi
    target=$(docker image inspect --format '{{.Id}}' "${IMAGE_REPO}@${new_digest}" 2>/dev/null || true)
    if [ -n "$target" ] && [ "$running" != "$target" ]; then
        log "${CONTAINER_NAME} runs another image than ${new_short}; left running."
        return 0
    fi
    log "stopping ${CONTAINER_NAME} (running ${new_short}, or an image whose ID could not be read) so the stamp read is final ..."
    if ! docker stop "$CONTAINER_NAME" >/dev/null; then
        log "could not stop ${CONTAINER_NAME}."
        return 1
    fi
    return 0
}

repin_is_safe() {
    # 0 when the previously-pinned image can resolve whatever revision the
    # database is stamped at NOW.  Asked AFTER a failure rather than before:
    # the deploy may have migrated before falling over, and this is the only
    # question whose answer decides whether re-pinning recovers or kills.
    # Asked after `stop_failed_container` too, and read FOR SHARE, so the
    # answer cannot change behind it.
    # It also distinguishes the failure cases for free -- a container that
    # never started, or whose entrypoint step 3 failed (one transaction since
    # plan step balance:X-cv), migrated nothing that stuck, so the stamp is
    # unchanged and the ordinary rollback still applies.
    #
    # STAMPED_REVISIONS becomes what the re-read found, or EMPTY when it found
    # nothing: the pre-flight's value describes the database before the
    # deploy, and `refuse_to_repin` must never report it as the database's
    # state after one (finding BAL-535).
    local stamped
    log "re-reading the stamp (waits for any step-3 transaction still ending) ..."
    # shellcheck disable=SC2310 ## boolean predicate, failure is the branch.
    if ! stamped=$(db_stamped_revisions) || [ -z "$stamped" ]; then
        STAMPED_REVISIONS=""
        log "could not re-read public.alembic_version after the failure;"
        log "treating the rollback as unsafe."
        return 1
    fi
    local stamped_line
    stamped_line=$(printf '%s' "$stamped" | tr '\n' ' ')
    log "database is now stamped at: ${stamped_line}"
    STAMPED_REVISIONS="$stamped"
    # shellcheck disable=SC2310 ## boolean predicate, failure is the branch.
    image_resolves_revisions "$OLD_MIGRATIONS" "$stamped"
}

take_dump() {
    # take_dump <label>: pg_dump -Fc the whole database into
    # shekel_prod_<label>_<ts>.dump, then prove the artifact is readable.
    # Sets DUMP_PATH.  Any failure is fatal and happens BEFORE the pin moves.
    # A deploy's label is predeploy_<target>; --dump-only's is
    # dump_<current pin>.
    #
    # Written to a .part file and renamed only after the archive DECODES: a
    # truncated dump that wears a valid name is worse than no dump, because it
    # is the thing the failure path tells the operator to restore from (the
    # partial-file discipline audit finding OPS/SH-04 established for the
    # backup family).  DUMP_PART is global so the EXIT trap sweeps a straggler
    # left by a Ctrl-C, which is OPS/SH-04's third part.
    #
    # The readback is `pg_restore -f /dev/null`, NOT `pg_restore -l`.  Measured
    # 2026-08-08 on a real 1.1 MB archive: -l reads only the table of contents,
    # which lives in the first ~8 KB, so archives truncated at 50% and even 99%
    # PASS it.  Restoring one of those after `--clean` empties the database and
    # then stops.  -f decodes every data block, which is the -Fc analogue of
    # the gzip integrity decode `_backup_lib.sh:validate_backup_artifact` does
    # for the nightly backups.
    local label="$1" ts
    ts=$(date +%Y%m%d_%H%M%S)
    DUMP_PATH="${BACKUP_DIR}/shekel_prod_${label}_${ts}.dump"
    DUMP_PART="${DUMP_PATH}.part"

    mkdir -p "$BACKUP_DIR" || die "cannot create backup directory $BACKUP_DIR."
    # shellcheck disable=SC2310 ## boolean predicate; see image_listing_or_die.
    if ! container_running "$DB_CONTAINER"; then
        die "database container '$DB_CONTAINER' is not running; no dump, no deploy."
    fi

    log "dumping ${DB_NAME} to ${DUMP_PATH} ..."
    # stderr is CAPTURED, not discarded: "pg_dump failed" without the reason
    # sends the operator hunting for a disk-full, an auth failure or a lock
    # timeout that the message already had in hand.
    local errfile status=0
    errfile=$(mktemp "${TMPDIR:-/tmp}/shekel-deploy-pgdump.XXXXXX")
    docker exec "$DB_CONTAINER" \
        pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc >"$DUMP_PART" 2>"$errfile" || status=$?
    if [ "$status" -ne 0 ]; then
        local reason
        reason=$(tr -d '\r' <"$errfile" | tail -3)
        rm -f "$errfile" "$DUMP_PART"
        DUMP_PART=""
        die "pg_dump exited ${status}; no dump, no deploy.  The pin has NOT
   been touched.  pg_dump said:
${reason}"
    fi
    rm -f "$errfile"

    if ! docker exec -i "$DB_CONTAINER" \
        pg_restore -f /dev/null <"$DUMP_PART" >/dev/null 2>&1; then
        rm -f "$DUMP_PART"
        DUMP_PART=""
        die "the dump was written but does not decode end to end -- treating
   it as truncated or corrupt.  No dump, no deploy."
    fi

    mv "$DUMP_PART" "$DUMP_PATH"
    DUMP_PART=""
    local bytes
    bytes=$(stat -c %s "$DUMP_PATH")
    log "dump OK: $DUMP_PATH (${bytes} bytes, decoded end to end)"
}

save_failed_container_log() {
    # Write the failed container's log beside the pre-deploy dump, BEFORE a
    # re-pin recreates the container.  Sets FAILED_LOG_PATH.
    #
    # Re-pinning runs `docker compose up -d` with the previous digest, which
    # REPLACES the container, and `docker logs` then shows the previous image
    # booting.  The failed run's own log is the one that says why it failed --
    # an init_database refusal names what to repair (plan step balance:X-cv made
    # such a refusal an automatic rollback) -- so it is kept as a file the
    # output and the ntfy alert can name.  Loki holds it too, but the operator
    # holding this script's output should not need to know that.
    #
    # Best effort, and deliberately so: the rollback does not wait on it past
    # the timeout.  A failure to save is logged and leaves FAILED_LOG_PATH
    # empty.
    FAILED_LOG_PATH="${DUMP_PATH%.dump}.failed-container.log"
    # The file's first line names the image the container ran: on the compose
    # path `compose up` may have failed before recreating it, and then this is
    # the PREVIOUS image's log, which the header makes visible.  Bounded, so a
    # hung daemon cannot hold the rollback.
    local image
    image=$(docker inspect --format '{{.Config.Image}}' "$CONTAINER_NAME" 2>/dev/null || true)
    if {
        printf '# docker logs %s (image: %s)\n' "$CONTAINER_NAME" "${image:-unknown}"
        timeout 30 docker logs "$CONTAINER_NAME" 2>&1
    } >"$FAILED_LOG_PATH"; then
        log "failed container's log saved: ${FAILED_LOG_PATH}"
    else
        rm -f "$FAILED_LOG_PATH"
        FAILED_LOG_PATH=""
        log "WARN: could not save ${CONTAINER_NAME}'s log before the container"
        log "      is recreated; it is in Loki (Grafana) only."
    fi
}

refuse_to_repin() {
    # The F-8 failure path: re-pinning the previous image is not known to
    # recover, so the pin stays at the new digest and the ways out are named.
    # `repin_is_safe` leaves one of three states, and each gets only the
    # claims it can support (finding BAL-535):
    #
    #   * STAMPED_REVISIONS holds a stamp the previous image cannot resolve and
    #     the NEW image can: the new image's step 3 COMMITTED, and the
    #     container failed later (a later entrypoint step, or the health
    #     check).  Starting it again restores nothing and loses nothing, which
    #     is the recovery when the failure was transient: a release that was
    #     only slower than the health check allowed (the container's own
    #     healthcheck turning unhealthy, or HEALTH_TIMEOUT_S running out)
    #     reaches here too, stopped by `stop_failed_container` as it came up.
    #     Going back to the previous image means restoring the dump.
    #   * STAMPED_REVISIONS holds a stamp NEITHER image can resolve: the new
    #     image's step 3 cannot have written it, so something other than this
    #     deploy moved it, and only the dump is named.
    #   * STAMPED_REVISIONS is EMPTY: the re-read failed, so whether step 3
    #     committed is unknown and nothing about it is claimed.  Once the
    #     database answers, the rollback goes through this script, whose
    #     pre-flight asks the stamp question again before writing anything.
    #
    # The failed container's log is saved first, as on the re-pin paths: the
    # restore below ends in a `compose up` that recreates the container, and
    # `docker logs` then shows the previous image.
    #
    # In the restore sequence the first instruction is to STOP the container,
    # and that ordering is not cosmetic: the app runs `restart: unless-stopped`
    # and re-runs `alembic upgrade head` on every start, so an operator who
    # begins with pg_restore is restoring underneath a container that is
    # concurrently migrating.  `stop_failed_container` has usually stopped it
    # already, but not on every path here -- a daemon that could not be asked
    # leaves both the container and the stamp unread -- so the instruction
    # stays first and unconditional.
    local why="$1"
    save_failed_container_log
    local failed_log="${FAILED_LOG_PATH:-not saved; docker logs ${CONTAINER_NAME}, or Loki}"
    log ""
    log "REFUSING to roll back."
    log "  ${why}"
    log ""
    if [ -z "$STAMPED_REVISIONS" ]; then
        log "  The stamp could not be re-read after the failure, so whether"
        log "  ${new_short}'s entrypoint step 3 committed its migrations is"
        log "  UNKNOWN, and re-pinning ${old_short} blind could make a second dead"
        log "  container.  The pin is left at ${new_short}."
        log ""
        log "  Failed container's log: ${failed_log}"
        log ""
        log "  Once the database answers, roll back through this script, whose"
        log "  pre-flight reads the stamp and refuses up front, naming the"
        log "  restore, if ${old_short} cannot resolve it:"
        log "    docker exec ${DB_CONTAINER} psql -U ${DB_USER} -d ${DB_NAME} \\"
        log "        -tAc 'SELECT version_num FROM public.alembic_version'"
        log "    $0 ${old_digest}"
        log ""
        log "  Pre-deploy dump:"
        log "    ${DUMP_PATH}"
        ntfy_notify 5 "Shekel deploy FAILED, rollback REFUSED" \
            "${new_short} did not come up and the stamp could not be re-read, so whether its migrations committed is unknown. Pin left at ${new_short}. Once the database answers, run: shekel-deploy ${old_digest}. Dump: ${DUMP_PATH}. Failed container's log: ${failed_log}"
        exit 1
    fi
    local stamped_line restart_hint=""
    stamped_line=$(printf '%s' "$STAMPED_REVISIONS" | tr '\n' ' ')
    log "  Database is now stamped at: ${stamped_line}"
    log "  ${old_short} has no migration script for that revision, so it would"
    log "  die at entrypoint step 3."
    # shellcheck disable=SC2310 ## boolean predicate, failure is the branch.
    if image_resolves_revisions "$NEW_MIGRATIONS" "$STAMPED_REVISIONS"; then
        log "  ${new_short}'s step 3 COMMITTED its migrations, and the container"
        log "  then failed later (a later entrypoint step, or the health check)."
        log "  The pin is left at ${new_short}."
        if [ -n "$ADDED_REVISIONS" ]; then
            log "  Migrations this release added:"
            printf '%s\n' "$ADDED_REVISIONS" | sed 's/^/    /'
        fi
        log ""
        log "  Read the failed container's log first -- it says which step failed:"
        log "    ${failed_log}"
        log ""
        log "  If the log shows a failure a restart can clear -- the release was"
        log "  only slower than the health check allowed, or a transient error --"
        log "  start ${new_short} again.  It resolves this stamp, so nothing is"
        log "  restored and nothing written since the dump is lost:"
        log "    cd ${SHEKEL_DIR} && docker compose up -d ${COMPOSE_SERVICE}"
        log ""
        log "  Otherwise, going back to ${old_short} means restoring the dump."
        restart_hint=" If the log shows a transient failure, start it again (nothing lost): cd ${SHEKEL_DIR} && docker compose up -d ${COMPOSE_SERVICE}. Otherwise restore."
    else
        log "  ${new_short} cannot resolve that revision either, so its step 3"
        log "  did not write it: something other than this deploy moved the"
        log "  stamp.  The pin is left at ${new_short}; restoring the dump is the"
        log "  way back.  The failed container's log: ${failed_log}"
    fi
    log ""
    log "  Pre-deploy dump:"
    log "    ${DUMP_PATH}"
    log ""
    log "  DATA LOSS WARNING: restoring that dump discards everything written"
    log "  since it was taken, a few minutes ago.  In a budgeting app that is"
    log "  real entries.  Dump the CURRENT state first so the window is"
    log "  recoverable:"
    log "    docker exec ${DB_CONTAINER} pg_dump -U ${DB_USER} -d ${DB_NAME} \\"
    log "        -Fc >'${DUMP_PATH}.failed-state'"
    log ""
    log "  Then, to restore and return to ${old_short}:"
    log "    cd ${SHEKEL_DIR}"
    log "    docker compose stop ${COMPOSE_SERVICE}   # FIRST: it re-migrates on every restart"
    log "    docker exec -i ${DB_CONTAINER} pg_restore -U ${DB_USER} \\"
    log "        -d ${DB_NAME} --clean --if-exists \\"
    log "        --single-transaction --exit-on-error <'${DUMP_PATH}'"
    log "    sed -i 's|^SHEKEL_IMAGE_DIGEST=.*|SHEKEL_IMAGE_DIGEST=${old_digest}|' ${ENV_FILE}"
    log "    docker compose up -d ${COMPOSE_SERVICE}"
    log ""
    log "  --single-transaction --exit-on-error matter: without them a stream"
    log "  that ends early leaves the database EMPTY after --clean has already"
    log "  dropped everything, with no transaction to roll back (OPS/SH-01)."
    log "  --clean also drops only what the archive knows about, so a table the"
    log "  failed migration created survives and will collide on the retry:"
    log "    docker exec ${DB_CONTAINER} psql -U ${DB_USER} -d ${DB_NAME} \\"
    log "        -c '\\dt budget.*'"

    ntfy_notify 5 "Shekel deploy FAILED, rollback REFUSED" \
        "${new_short} did not come up and ${old_short} cannot resolve the schema the database is now at (${stamped_line}), so re-pinning cannot work. Pin left at ${new_short}. Read the failed container's log (${failed_log}) first.${restart_hint} Before any restore, run: docker compose stop ${COMPOSE_SERVICE} (it re-migrates on every restart). Dump: ${DUMP_PATH}"
    exit 1
}

refuse_unstoppable() {
    # Ruling R-BAL119's refusal: the target image's container could not be
    # stopped, so its entrypoint step 3 may still commit a migration and no
    # stamp read is final yet.  Re-pinning on such a read can produce the F-8
    # pair of dead containers, so the pin stays and the operator re-runs the
    # decision once the container is down -- through this script's own
    # pre-flight, the one place that question is asked before anything is
    # written, which refuses up front and names the restore if the previous
    # image cannot resolve the stamp.
    local why="$1"
    log ""
    log "REFUSING to roll back."
    log "  ${why}"
    log ""
    log "  ${CONTAINER_NAME} could not be stopped, and it may be running"
    log "  ${new_short}'s entrypoint step 3, which may still commit a migration:"
    log "  the stamp cannot be read as final.  The pin is left at ${new_short}."
    log ""
    log "  Stop it, then roll back through this script, whose pre-flight"
    log "  re-reads the stamp and refuses up front if ${old_short} cannot"
    log "  resolve it:"
    log "    docker stop ${CONTAINER_NAME}"
    log "    $0 ${old_digest}"
    log ""
    log "  Pre-deploy dump:"
    log "    ${DUMP_PATH}"

    ntfy_notify 5 "Shekel deploy FAILED, rollback REFUSED" \
        "${new_short} did not come up and its container could not be stopped, so the stamp read would not be final. Pin left at ${new_short}. Stop ${CONTAINER_NAME}, then run: shekel-deploy ${old_digest}. Dump: ${DUMP_PATH}"
    exit 1
}

wait_healthy() {
    local elapsed=0 status
    while [ "$elapsed" -lt "$HEALTH_TIMEOUT_S" ]; do
        status=$(docker inspect "$CONTAINER_NAME" \
            --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
            2>/dev/null || echo missing)
        case "$status" in
            healthy)
                log "container healthy after ${elapsed}s."
                return 0
                ;;
            unhealthy)
                log "container reported unhealthy after ${elapsed}s."
                return 1
                ;;
            starting) : ;;
            *) log "health status: $status" ;;
        esac
        sleep "$HEALTH_INTERVAL_S"
        elapsed=$((elapsed + HEALTH_INTERVAL_S))
    done
    log "timed out waiting for healthy after ${HEALTH_TIMEOUT_S}s."
    return 1
}

compose_up() {
    (cd "$SHEKEL_DIR" && docker compose up -d "$COMPOSE_SERVICE")
}

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
    log "  Entrypoint step 3 runs them, the seeds and the audit-trigger check"
    log "  in ONE transaction, so a failure there leaves the"
    log "  stamp where ${old_short} can resolve it and the rollback applies."
    log "  If step 3 COMMITS and a later step or the health check then fails,"
    log "  re-pinning ${old_short} will NOT work.  This script re-reads the stamp"
    log "  after a failure and refuses rather than making a second dead container."
else
    log "the target adds no migrations, so a failure leaves the schema where"
    log "${old_short} can still resolve it and the rollback applies as usual."
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
    # `docker compose up -d` reporting failure USUALLY means the container
    # never started, so nothing migrated -- but it also covers one that
    # started and exited having reached entrypoint step 3.  Rather than guess
    # from the exit status, ask the database: if the stamp is still something
    # ${old_short} can resolve, the ordinary revert is correct.  Asked once a
    # target-image container is stopped, so the answer is final.
    # shellcheck disable=SC2310 ## boolean predicate, failure is the branch.
    if ! stop_failed_container; then
        refuse_unstoppable "docker compose up rejected ${new_short}."
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
        "compose up rejected ${new_short}; reverted to ${old_short}. Failed container's log: ${FAILED_LOG_PATH:-in Loki only}"
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
# entrypoint step 3 COMMITTED its migrations before something later failed --
# has no working rollback, so it gets the refusal instead of a second dead
# container.  A step-3 failure left the stamp unmoved (plan step X-cv) and
# re-pins below.  The container is stopped before the stamp is read, and the
# read waits for any commit it already sent, so a step 3 still in flight cannot
# commit behind the read (ruling R-BAL115).
# shellcheck disable=SC2310 ## boolean predicate, failure is the branch.
if ! stop_failed_container; then
    refuse_unstoppable "${new_short} did not report healthy within ${HEALTH_TIMEOUT_S}s."
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
    log "failed container's log: ${FAILED_LOG_PATH:-in Loki only}"
    ntfy_notify 5 "Shekel deploy FAILED, rolled back" \
        "${new_short} did not become healthy. Reverted to ${old_short}. Failed container's log: ${FAILED_LOG_PATH:-in Loki only}"
    exit 1
fi

# Rollback also unhealthy -- manual intervention required.  Reached only when
# the previous image CAN resolve the current stamp, so the migration level is
# not the suspect; something both images meet is (schema drift the stamp does
# not record, the data, or the environment).  The dump is the recovery only
# when the failed release's step 3 committed what the previous image now
# refuses; docs/runbook.md s.2.3.  Also reached when the re-pin's own
# `compose up` failed and usually nothing started (finding BAL-540).
log "pre-deploy dump kept at: ${DUMP_PATH}"
log "failed container's log: ${FAILED_LOG_PATH:-in Loki only}"
ntfy_notify 5 "Shekel deploy FAILED, rollback UNHEALTHY" \
    "Neither ${new_short} nor rollback ${old_short} is healthy. Container needs manual attention. Dump: ${DUMP_PATH}. Failed container's log: ${FAILED_LOG_PATH:-in Loki only}"
die "rollback container also unhealthy; manual intervention required."
