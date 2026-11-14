#!/bin/bash
# Shekel Budget App -- deploy/shekel-deploy.sh's helpers: logging and the
# alert, the pin, the images' migration listings and the stamp, the stop
# that makes the stamp read final, the dump, the failed container's log,
# and the health wait.
# SOURCE this file; do not execute it.
#
# A part of deploy/shekel-deploy.sh, which loads it from beside itself
# (ruling plan#3544: no file over 1,000 lines), declares every global the
# parts share, and says in its header why each check exists.
# _deploy_refusals.sh, the other part, holds the two refusals.
#
# Read alone, as shellcheck reads each file, a part cannot see the globals the
# entry assigns or reads, so the function that first touches one carries a
# directive naming it (shellcheck reports a variable once, at its first
# reference, so a later function's directive would suppress nothing):
# SC2154 for a global the entry assigns (new_digest, new_short, old_digest,
# old_short, _self), SC2034 for one only another file reads.  The entry's
# own check follows its `source` lines for definitions and reports nothing
# inside them (measured 2026-11-13, with a typo planted in a copy of each
# part), so this file's check is its only one.

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

# shellcheck disable=SC2154 ## _self: the entry's; see this file's header.
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

# shellcheck disable=SC2034 ## ADDED_REVISIONS: read by the entry and
# _deploy_refusals.sh; see this file's header.
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

# shellcheck disable=SC2154 ## new_digest and new_short: the entry's; see
# this file's header.
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
        # shellcheck disable=SC2312 ## the entry sets pipefail, which this
        # file, read alone, cannot show.
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
