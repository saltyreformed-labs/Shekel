#!/bin/bash
# Shekel Budget App -- deploy/shekel-deploy.sh's three refusals to roll back:
# the stamp the previous image cannot resolve (F-8), the previous image that
# cannot run under the compose files in place, and the container that could
# not be stopped.
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
# inside them (measured 2026-11-11, with a typo planted in a copy of
# _deploy_lib.sh), so this file's check is its only one.

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
    #     deploy failed later (its step 4, the first user's seed, or the
    #     health check).  Starting it again restores nothing and loses nothing, which
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
    # The failed containers' logs are saved first, as on the re-pin paths: the
    # restore below ends in a `compose up` that recreates both containers, and
    # `docker logs` then shows the previous image.
    #
    # In the restore sequence the first instruction is to STOP the app, and
    # that ordering is not cosmetic: a live app writes, so an operator who
    # begins with pg_restore is restoring underneath requests that write.  The
    # app restarting runs no deploy (only an `up` runs the migrate service,
    # which is `restart: "no"`), and the sequence's closing `up` runs the
    # previous image's deploy against the restored database before it starts
    # the app.  `stop_failed_container` has usually stopped the app already,
    # but not on every path here -- a daemon that could not be asked leaves
    # both the container and the stamp unread, and a stop that failed only
    # warned (ruling plan#3539) -- so the instruction stays first and
    # unconditional.
    local why="$1"
    save_failed_container_log
    local failed_log="${FAILED_LOG_PATH:-not saved; docker logs ${MIGRATE_CONTAINER} and ${CONTAINER_NAME}, or Loki}"
    log ""
    log "REFUSING to roll back."
    log "  ${why}"
    log ""
    if [ -z "$STAMPED_REVISIONS" ]; then
        log "  The stamp could not be re-read after the failure, so whether"
        log "  ${new_short}'s step 3, in the migrate service, committed its"
        log "  migrations is UNKNOWN, and re-pinning ${old_short} blind could make a"
        log "  second dead container.  The pin is left at ${new_short}."
        log ""
        log "  Failed containers' logs: ${failed_log}"
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
            "${new_short} did not come up and the stamp could not be re-read, so whether its migrations committed is unknown. Pin left at ${new_short}. Once the database answers, run: shekel-deploy ${old_digest}. Dump: ${DUMP_PATH}. Failed containers' logs: ${failed_log}"
        exit 1
    fi
    local stamped_line restart_hint=""
    stamped_line=$(printf '%s' "$STAMPED_REVISIONS" | tr '\n' ' ')
    log "  Database is now stamped at: ${stamped_line}"
    log "  ${old_short} has no migration script for that revision, so its deploy"
    log "  would die at step 3, in the migrate service."
    # shellcheck disable=SC2310 ## boolean predicate, failure is the branch.
    if image_resolves_revisions "$NEW_MIGRATIONS" "$STAMPED_REVISIONS"; then
        log "  ${new_short}'s step 3 COMMITTED its migrations, and the deploy then"
        log "  failed later (its step 4, the first user's seed, or the health check)."
        log "  The pin is left at ${new_short}."
        if [ -n "$ADDED_REVISIONS" ]; then
            log "  Migrations this release added:"
            printf '%s\n' "$ADDED_REVISIONS" | sed 's/^/    /'
        fi
        log ""
        log "  Read the failed containers' logs first -- the migrate container's"
        log "  says which deploy step failed:"
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
        log "  way back.  The failed containers' logs: ${failed_log}"
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
    log "    docker compose stop ${COMPOSE_SERVICE}   # FIRST: a live app writes during the restore"
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
        "${new_short} did not come up and ${old_short} cannot resolve the schema the database is now at (${stamped_line}), so re-pinning cannot work. Pin left at ${new_short}. Read the failed containers' logs (${failed_log}) first.${restart_hint} Before any restore, run: docker compose stop ${COMPOSE_SERVICE} (a live app writes during the restore). Dump: ${DUMP_PATH}"
    exit 1
}

refuse_layout() {
    # The pre-flight found that the previous image cannot run under the
    # compose files in place (`image_runs_this_layout`): their migrate service
    # runs an entrypoint it does not hold, so a re-pin would fail its `up` and
    # leave nothing serving.  The first release of card plan#2166 is this
    # case.  So the pin stays at the new digest, and the way back is named:
    # the compose files the previous image ran under as well as its digest,
    # which this script does not hold.  The stamp is re-read as on every
    # failure path, for the one more thing the way back turns on: whether the
    # previous image resolves it, or the dump must be restored first.
    local why="$1" resolved=false stamped_line
    # shellcheck disable=SC2310 ## the answer selects the text; both continue.
    if repin_is_safe; then
        resolved=true
    fi
    stamped_line=$(printf '%s' "$STAMPED_REVISIONS" | tr '\n' ' ')
    save_failed_container_log
    local failed_log="${FAILED_LOG_PATH:-not saved; docker logs ${MIGRATE_CONTAINER} and ${CONTAINER_NAME}, or Loki}"
    log ""
    log "REFUSING to roll back."
    log "  ${why}"
    log ""
    log "  ${old_short} cannot run under the compose files in place: their migrate"
    log "  service runs ${MIGRATE_ENTRYPOINT}, which ${old_short} does not hold,"
    log "  so re-pinning it would bring nothing up.  The pin is left at ${new_short}."
    log ""
    log "  Failed containers' logs: ${failed_log}"
    log ""
    log "  Going back to ${old_short} takes the compose files it ran under as well"
    log "  as its digest (${old_digest}); this script changes the digest alone."
    if $resolved; then
        log "  The database is stamped at ${stamped_line}, which ${old_short}"
        log "  resolves, so it needs no restore."
    elif [ -z "$STAMPED_REVISIONS" ]; then
        log "  The stamp could not be re-read, so whether ${old_short} resolves it is"
        log "  UNKNOWN: read it before going back, and restore the dump first if"
        log "  ${old_short} has no migration script for it."
    else
        log "  The database is stamped at ${stamped_line}, which ${old_short} has no"
        log "  migration script for, so the dump must be restored before it starts."
    fi
    log ""
    log "  Pre-deploy dump:"
    log "    ${DUMP_PATH}"

    ntfy_notify 5 "Shekel deploy FAILED, rollback REFUSED" \
        "${new_short} did not come up, and ${old_short} cannot run under the compose files in place (no ${MIGRATE_ENTRYPOINT}), so re-pinning cannot work. Pin left at ${new_short}. Going back takes ${old_short}'s compose files as well as its digest. Failed containers' logs: ${failed_log}. Dump: ${DUMP_PATH}"
    exit 1
}

refuse_unstoppable() {
    # Ruling R-BAL119's refusal: the migrate container, still running the
    # deploy, could not be stopped, so its step 3 may still commit a
    # migration and no stamp read is final yet.  Re-pinning on such a read can
    # produce the F-8 pair of dead containers, so the pin stays and the
    # operator re-runs the decision once the container is down -- through this
    # script's own pre-flight, the one place that question is asked before
    # anything is written, which refuses up front and names the restore if the
    # previous image cannot resolve the stamp.  The app container never
    # reaches here: it cannot move the stamp, so a stop of it that fails is a
    # warning (ruling plan#3539, `stop_failed_container`).
    local why="$1"
    log ""
    log "REFUSING to roll back."
    log "  ${why}"
    log ""
    log "  ${MIGRATE_CONTAINER} could not be stopped, and it may be running"
    log "  ${new_short}'s deploy, whose step 3 may still commit a migration:"
    log "  the stamp cannot be read as final.  The pin is left at ${new_short}."
    log ""
    log "  Stop it, then roll back through this script, whose pre-flight"
    log "  re-reads the stamp and refuses up front if ${old_short} cannot"
    log "  resolve it:"
    log "    docker stop ${MIGRATE_CONTAINER}"
    log "    $0 ${old_digest}"
    log ""
    log "  Pre-deploy dump:"
    log "    ${DUMP_PATH}"

    ntfy_notify 5 "Shekel deploy FAILED, rollback REFUSED" \
        "${new_short} did not come up and its deploy (${MIGRATE_CONTAINER}) could not be stopped, so the stamp read would not be final. Pin left at ${new_short}. Stop ${MIGRATE_CONTAINER}, then run: shekel-deploy ${old_digest}. Dump: ${DUMP_PATH}"
    exit 1
}
