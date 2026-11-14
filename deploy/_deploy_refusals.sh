#!/bin/bash
# Shekel Budget App -- deploy/shekel-deploy.sh's two refusals to roll back:
# the stamp the previous image cannot resolve (F-8), and the container that
# could not be stopped.
# SOURCE this file; do not execute it.
#
# A part of deploy/shekel-deploy.sh, which loads it from beside itself
# (ruling plan#3544: no file over 1,000 lines), declares every global the
# parts share, and says in its header why each refusal exists.
# _deploy_lib.sh, the other part, holds the helpers these call.
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

# shellcheck disable=SC2154 ## new_short, old_short and old_digest: the
# entry's; see this file's header.
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
