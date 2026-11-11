"""A failed deploy: the re-pin a migration-bearing release refuses and a pure revert keeps, a
step-3 refusal rolled back with its log saved, the stamp read once the target is stopped, and
the refusal saying only what the stamp shows.
"""

from __future__ import annotations

from ._helpers import (
    _DEPLOY_SCRIPT,
    _OLD,
    _NEW,
    _image_id,
    _flat,
    _said,
    _position,
    _MIGRATION_BEARING,
    _NO_MIGRATIONS,
    _STEP_3_ROLLED_BACK,
    _REFUSAL_LINE,
    _last_stamp_read,
    _compose_ups,
    _FAILURE_PATHS,
    _refusal,
    _NEW_SHORT,
    _as_read,
)


class TestTheMigrationBearingReleaseIsNotRePinned:
    """Finding F-8: for such a release, re-pinning is what cannot work."""

    def test_it_refuses_and_leaves_the_pin_at_the_new_digest(self, stack):
        """A failed migration-bearing deploy does not revert the pin."""
        result = stack.run(health="unhealthy", **_MIGRATION_BEARING)
        output = result.stdout + result.stderr
        assert result.returncode == 1
        assert "REFUSING to roll back" in output
        assert stack.pin == _NEW, (
            "the script re-pinned the previous digest for a migration-bearing "
            "release -- that is finding F-8: the old image cannot resolve the "
            "revision the database is now stamped at, so it dies too."
        )

    def test_the_refusal_names_the_dump_that_exists(self, stack):
        """The dump path it prints is a file that is actually on disk."""
        result = stack.run(health="unhealthy", **_MIGRATION_BEARING)
        output = result.stdout + result.stderr
        # **The message renders what the script SAID**, because this assertion
        # firing bare is what cost a 47-minute CI cycle its own diagnosis on
        # 2026-09-03: ``output`` was bound one line above, held the answer, and
        # was not read.  ``shekel-deploy.sh`` refuses with one of three named
        # sentences -- a non-zero ``pg_dump``, a dump that does not decode end
        # to end, or a database container that is not running -- and any of
        # them settles in one line what an ``assert 0 == 1`` cannot.
        #
        # The directory listing is the FOURTH case: both refusals that reach
        # here delete the ``.part`` before dying, so a ``.part`` surviving means
        # a path nobody has enumerated.
        assert len(stack.dumps) == 1, (
            f"no dump artifact was written, so the refusal has nothing to "
            f"name.  The script exited {result.returncode} and said:\n{output}"
            f"\nbackup dir holds: "
            f"{sorted(p.name for p in stack.backup_dir.iterdir())}"
        )
        assert str(stack.dumps[0]) in output, (
            "the refusal does not name the dump it took, so the operator has "
            "nothing to restore from"
        )
        assert "pg_restore" in output, "no restore command was given"
        assert "0002_b.py" in output and "0003_c.py" in output, (
            "the refusal does not name the migrations the release added"
        )

    def test_a_compose_failure_also_refuses(self, stack):
        """Fail closed on the other failure path too.

        ``docker compose up -d`` failing usually means the container never
        started, but it also covers one that started and exited having reached
        the migration step.  The two are indistinguishable from the exit
        status, so neither re-pins.
        """
        result = stack.run(compose_rc="1", **_MIGRATION_BEARING)
        assert result.returncode == 1
        assert "REFUSING to roll back" in result.stdout + result.stderr
        assert stack.pin == _NEW


class TestTheOrdinaryRollbackSurvives:
    """R-R14 forbids removing it: 6 of the last 10 releases were pure reverts."""

    def test_a_release_with_no_new_migrations_reverts_the_pin(self, stack):
        """The automatic rollback still runs, and puts the old digest back."""
        result = stack.run(health="unhealthy", **_NO_MIGRATIONS)
        output = result.stdout + result.stderr
        assert result.returncode == 1
        assert "REFUSING to roll back" not in output, (
            "the script refused to roll back a release that adds no "
            "migrations; that rollback works and the ruling keeps it"
        )
        assert "rolling back to" in output
        assert stack.pin == _OLD, (
            "the ordinary rollback did not restore the previous digest"
        )

    def test_a_healthy_deploy_keeps_the_new_pin_and_the_dump(self, stack):
        """The success path is unchanged and the restore point is kept."""
        result = stack.run(health="healthy", **_NO_MIGRATIONS)
        assert result.returncode == 0
        assert stack.pin == _NEW
        assert len(stack.dumps) == 1
        assert str(stack.dumps[0]) in result.stdout


class TestAStep3RefusalRollsBack:
    """Plan step balance:X-cv: a step-3 refusal re-pins, and its log is kept.

    The deploy's step 3 is one transaction, so a migration-bearing release whose
    deploy hook refuses leaves the stamp where the previous image can resolve
    it.  The script's direction-free question then answers "re-pin" with no
    change of its own -- and re-pinning RECREATES the container, so the failed
    run's log, which names what to repair, is saved beside the dump first.
    """

    def test_a_migration_bearing_release_with_an_unmoved_stamp_re_pins(
        self, stack,
    ):
        """The target added a migration, the stamp never moved: roll back.

        A PIN, not evidence for X-cv: the script's re-pin question is unchanged
        (it passes on the pre-X-cv script too).  What X-cv changed is that a
        step-3 failure now PRODUCES this unmoved stamp; the one-transaction
        tests and the clone rehearsal grade that half.
        """
        result = stack.run(
            health="unhealthy", container_log=_REFUSAL_LINE,
            **_STEP_3_ROLLED_BACK,
        )
        output = result.stdout + result.stderr
        assert result.returncode == 1
        assert "MIGRATION-BEARING release" in output
        assert "REFUSING to roll back" not in output
        assert stack.pin == _OLD

    def test_the_failed_log_is_saved_beside_the_dump_before_the_re_pin(
        self, stack,
    ):
        """The file holds the refusal, is named, and was read before recreation."""
        result = stack.run(
            health="unhealthy", container_log=_REFUSAL_LINE,
            **_STEP_3_ROLLED_BACK,
        )
        output = result.stdout + result.stderr
        saved = sorted(stack.backup_dir.glob("*.failed-container.log"))
        assert len(saved) == 1, (
            f"no failed-container log beside the dump.  The script said:\n"
            f"{output}"
        )
        assert len(stack.dumps) == 1, output
        assert saved[0].name == stack.dumps[0].name.replace(
            ".dump", ".failed-container.log",
        )
        assert _REFUSAL_LINE in saved[0].read_text(encoding="utf-8")
        assert str(saved[0]) in output
        # Read BEFORE the re-pin's `compose up` replaced the container.
        calls = stack.invocations
        read_at = _position(calls, "logs probe-app", result)
        compose_ups = _compose_ups(calls, result)
        assert len(compose_ups) == 2, _said(result, calls)
        assert compose_ups[0] < read_at < compose_ups[1], _said(result, calls)

    def test_a_compose_failure_saves_the_log_too(self, stack):
        """The other re-pin path keeps the log as well."""
        result = stack.run(
            compose_rc="1", container_log=_REFUSAL_LINE,
            **_STEP_3_ROLLED_BACK,
        )
        assert result.returncode == 1
        assert stack.pin == _OLD
        saved = sorted(stack.backup_dir.glob("*.failed-container.log"))
        assert len(saved) == 1
        assert _REFUSAL_LINE in saved[0].read_text(encoding="utf-8")

    def test_a_log_that_cannot_be_read_does_not_stop_the_rollback(
        self, stack,
    ):
        """No log to save: the re-pin still happens, and says where the log is."""
        result = stack.run(
            health="unhealthy", logs_rc="1", **_STEP_3_ROLLED_BACK,
        )
        output = result.stdout + result.stderr
        assert stack.pin == _OLD
        assert "could not save probe-app's log" in output
        assert "could not save probe-migrate's log" in output
        assert "failed containers' logs: in Loki only" in output
        assert list(stack.backup_dir.glob("*.failed-container.log")) == []

    def test_the_saved_file_holds_the_deploy_s_log_then_the_app_s(self, stack):
        """The migrate container's log, which names the refusal, comes first.

        Since card plan#2166 the deploy runs in the migrate service, so its
        log, not the app's, says why step 3 failed.  Each log sits under a
        header naming its container, in the order they ran.
        """
        result = stack.run(
            health="unhealthy", migrate_log=_REFUSAL_LINE,
            container_log="fake app log", **_STEP_3_ROLLED_BACK,
        )
        calls = stack.invocations
        saved = sorted(stack.backup_dir.glob("*.failed-container.log"))
        assert len(saved) == 1, _said(result, calls)
        text = saved[0].read_text(encoding="utf-8")
        migrate_at = text.index("# docker logs probe-migrate (image: ")
        app_at = text.index("# docker logs probe-app (image: ")
        assert migrate_at < text.index(_REFUSAL_LINE) < app_at, text
        assert app_at < text.index("fake app log"), text
        assert f"failed containers' logs saved: {saved[0]}" in result.stdout, (
            _said(result, calls))

    def test_one_log_that_cannot_be_read_keeps_the_other(self, stack):
        """The migrate container's log fails: the file keeps the app's, and says so."""
        result = stack.run(
            health="unhealthy", migrate_logs_rc="1", container_log="fake app log",
            **_STEP_3_ROLLED_BACK,
        )
        output = result.stdout + result.stderr
        calls = stack.invocations
        saved = sorted(stack.backup_dir.glob("*.failed-container.log"))
        assert len(saved) == 1, _said(result, calls)
        text = saved[0].read_text(encoding="utf-8")
        assert "# docker logs probe-migrate exited 1: no log saved" in text, text
        assert "fake app log" in text, text
        assert "could not save probe-migrate's log" in output, _said(result, calls)
        assert "could not save probe-app's log" not in output, _said(result, calls)
        assert stack.pin == _OLD, _said(result, calls)


class TestTheStampIsReadOnceTheTargetIsStopped:
    """Plan step balance:X-cv, rulings R-BAL115 and R-BAL119: the re-read is final.

    A container running the TARGET image may still be inside entrypoint step 3,
    whose one transaction can commit the new stamp after the script has read
    the old one, and the re-pin would then meet finding F-8's second dead
    container.  So on both failure paths that container is stopped first, and
    the ruled order is pinned: stop, read the stamp, save the log, re-pin.  A
    container that does not run the target (absent, or the previous one that
    compose failed before replacing) cannot move the stamp and is left alone.
    """

    @_FAILURE_PATHS
    def test_it_stops_then_reads_then_saves_then_re_pins(self, stack, failure):
        """Both failure paths run the four steps in the ruled order.

        And the re-read asks FOR SHARE, so it waits for a COMMIT the stopped
        container had already sent (the stub cannot model the lock, so this
        pins that the question is asked that way).
        """
        result = stack.run(**failure, **_STEP_3_ROLLED_BACK)
        calls = stack.invocations
        assert stack.pin == _OLD, _said(result, calls)
        stopped_at = _position(calls, "stop probe-app", result)
        saved_at = _position(calls, "logs probe-app", result)
        re_pinned_at = _compose_ups(calls, result)[-1]
        read_at = _last_stamp_read(calls, result)
        assert stopped_at < read_at < saved_at < re_pinned_at, (
            _said(result, calls)
        )
        assert calls[read_at].endswith("FROM public.alembic_version FOR SHARE")

    def test_a_target_whose_id_cannot_be_read_is_still_stopped(self, stack):
        """No target ID to compare with: stop, since downtime is the cheaper error."""
        result = stack.run(
            health="unhealthy", target_image_id="", **_STEP_3_ROLLED_BACK,
        )
        calls = stack.invocations
        stopped_at = _position(calls, "stop probe-app", result)
        assert stopped_at < _last_stamp_read(calls, result), (
            _said(result, calls)
        )
        assert stack.pin == _OLD, _said(result, calls)

    def test_the_previous_container_compose_never_replaced_keeps_serving(
        self, stack,
    ):
        """Compose failed before replacing it: it is not stopped, and the decision runs."""
        result = stack.run(
            compose_rc="1", container_image_id=_image_id(_OLD),
            **_STEP_3_ROLLED_BACK,
        )
        calls = stack.invocations
        assert "stop probe-app" not in calls, _said(result, calls)
        assert (
            _last_stamp_read(calls, result) > _compose_ups(calls, result)[0]
        ), _said(result, calls)
        assert stack.pin == _OLD, _said(result, calls)

    def test_an_absent_container_is_not_stopped(self, stack):
        """No container to stop: the stamp is read and the re-pin runs."""
        result = stack.run(
            compose_rc="1", container_image_id="", **_STEP_3_ROLLED_BACK,
        )
        calls = stack.invocations
        assert "stop probe-app" not in calls, _said(result, calls)
        assert (
            _last_stamp_read(calls, result) > _compose_ups(calls, result)[0]
        ), _said(result, calls)
        assert stack.pin == _OLD, _said(result, calls)

    @_FAILURE_PATHS
    def test_a_target_container_that_will_not_stop_refuses_the_re_pin(
        self, stack, failure,
    ):
        """Its step 3 may still commit, so no read is final: the pin stays, loudly.

        No stamp is read after the failed stop, and nothing is re-pinned.  The
        refusal names the roll-back through this script, whose pre-flight asks
        the stamp question again once the container is down.
        """
        result = stack.run(stop_rc="1", **failure, **_STEP_3_ROLLED_BACK)
        output = result.stdout + result.stderr
        calls = stack.invocations
        assert result.returncode == 1
        assert "REFUSING to roll back" in output
        assert "could not be stopped" in output
        assert stack.pin == _NEW
        assert (
            _last_stamp_read(calls, result)
            < _position(calls, "stop probe-app", result)
        ), _said(result, calls)
        assert len(_compose_ups(calls, result)) == 1, _said(result, calls)
        assert f"{_DEPLOY_SCRIPT} {_OLD}" in _flat(output)


class TestARunningDeployIsStoppedBeforeTheRead:
    """Card plan#2166: the migrate container, where step 3 now runs, is stopped first.

    ``docker compose up -d app`` runs the migrate service and waits for it, so
    a ``up`` that fails with the deploy still running leaves that container
    inside step 3, whose one transaction may yet commit.  It is stopped before
    the stamp is read, whatever image it runs; one that has exited can commit
    nothing and is left alone.
    """

    def test_a_deploy_still_running_is_stopped_before_the_stamp_read(self, stack):
        """Stop the migrate container, then read the stamp, then re-pin."""
        result = stack.run(
            compose_rc="1", migrate_running="true", **_STEP_3_ROLLED_BACK,
        )
        calls = stack.invocations
        stopped_at = _position(calls, "stop probe-migrate", result)
        assert stopped_at < _last_stamp_read(calls, result), _said(result, calls)
        assert _compose_ups(calls, result)[0] < stopped_at, _said(result, calls)
        assert stack.pin == _OLD, _said(result, calls)

    @_FAILURE_PATHS
    def test_an_exited_migrate_container_is_not_stopped(self, stack, failure):
        """A deploy that has ended can commit nothing: no stop, and its log is saved."""
        result = stack.run(migrate_log=_REFUSAL_LINE, **failure, **_STEP_3_ROLLED_BACK)
        calls = stack.invocations
        assert "stop probe-migrate" not in calls, _said(result, calls)
        assert "logs probe-migrate" in calls, _said(result, calls)
        assert stack.pin == _OLD, _said(result, calls)

    def test_a_migrate_container_that_will_not_stop_refuses_the_re_pin(
        self, stack,
    ):
        """Its step 3 may still commit, so no read is final: the pin stays, loudly.

        The refusal names the container that would not stop and the way back
        through this script, and no stamp is read after the failed stop.
        """
        result = stack.run(
            compose_rc="1", migrate_running="true", migrate_stop_rc="1",
            **_STEP_3_ROLLED_BACK,
        )
        output = result.stdout + result.stderr
        calls = stack.invocations
        assert result.returncode == 1, _said(result, calls)
        assert "REFUSING to roll back" in output, _said(result, calls)
        assert "probe-migrate could not be stopped" in _flat(output), (
            _said(result, calls))
        assert "docker stop probe-migrate" in output, _said(result, calls)
        assert stack.pin == _NEW, _said(result, calls)
        assert (
            _last_stamp_read(calls, result)
            < _position(calls, "stop probe-migrate", result)
        ), _said(result, calls)
        assert len(_compose_ups(calls, result)) == 1, _said(result, calls)


class TestAPreviousImageThatCannotRunHereIsNotRePinned:
    """Card plan#2166: a re-pin is a recovery only under compose files the image can run.

    The migrate service runs ``scripts/owner_entrypoint.sh`` from the app's
    image, and an image built before the card has none, so re-pinning one
    fails its ``up`` and leaves nothing serving.  The first release of the
    card is that case: a failure is refused, the pin stays at the new digest,
    and the way back names the compose files as well as the digest, and
    whether the stamp it re-reads needs the dump restored first.
    """

    @_FAILURE_PATHS
    def test_it_refuses_and_names_the_compose_files(self, stack, failure):
        """An unmoved stamp: refused, one ``up``, the logs saved, no restore needed."""
        result = stack.run(old_layout_rc="1", **failure, **_NO_MIGRATIONS)
        output = result.stdout + result.stderr
        calls = stack.invocations
        refusal = _as_read(_refusal(output))
        assert result.returncode == 1, _said(result, calls)
        assert stack.pin == _NEW, _said(result, calls)
        assert len(_compose_ups(calls, result)) == 1, _said(result, calls)
        assert "cannot run under the compose files in place" in refusal
        assert "/home/shekel/app/scripts/owner_entrypoint.sh" in refusal
        assert "takes the compose files it ran under as well as its digest" in refusal
        assert f"stamped at 0002, which {_OLD[7:19]} resolves, so it needs no restore" in (
            refusal)
        saved = sorted(stack.backup_dir.glob("*.failed-container.log"))
        assert len(saved) == 1, _said(result, calls)
        assert str(saved[0]) in refusal, _said(result, calls)

    def test_a_stamp_it_cannot_resolve_names_the_restore(self, stack):
        """The target's step 3 committed: the dump must be restored before it starts."""
        result = stack.run(
            health="unhealthy", old_layout_rc="1", **_MIGRATION_BEARING,
        )
        refusal = _as_read(_refusal(result.stdout + result.stderr))
        assert stack.pin == _NEW
        assert (
            f"stamped at 0003, which {_OLD[7:19]} has no migration script for, "
            "so the dump must be restored before it starts" in refusal
        ), refusal
        assert str(stack.dumps[0]) in refusal

    def test_an_unread_stamp_is_named_unknown(self, stack):
        """The re-read fails: whether a restore is needed is not claimed."""
        result = stack.run(
            health="unhealthy", old_layout_rc="1", stamp_reread_rc="1",
            **_MIGRATION_BEARING,
        )
        refusal = _as_read(_refusal(result.stdout + result.stderr))
        assert stack.pin == _NEW
        assert "The stamp could not be re-read" in refusal, refusal
        assert "UNKNOWN" in refusal, refusal
        assert "needs no restore" not in refusal, refusal


class TestTheRefusalSaysOnlyWhatTheStampShows:
    """Finding BAL-535: the refusal's diagnosis comes from the stamp it re-read.

    It used to say the new image "COMMITTED its migrations" and print the
    PRE-FLIGHT's stamp as the database's state whatever brought it there --
    a re-read that FAILED included, where nothing is known -- and it named
    only a restore that discards data, even for a release that was merely
    slower than the health window, which starting the new pin again recovers
    with nothing lost.
    """

    def test_an_unreadable_stamp_claims_no_commit_and_prints_no_stale_stamp(
        self, stack,
    ):
        """The re-read fails: nothing about a commit is claimed, no stamp is printed.

        The pin stays and the way back is this script's own pre-flight, which
        reads the stamp again before writing anything.
        """
        result = stack.run(
            health="unhealthy", stamp_reread_rc="1", **_MIGRATION_BEARING,
        )
        output = result.stdout + result.stderr
        refusal = _refusal(output)
        assert result.returncode == 1, output
        assert stack.pin == _NEW
        calls = stack.invocations
        assert len(_compose_ups(calls, result)) == 1, _said(result, calls)
        assert "The stamp could not be re-read after the failure" in refusal
        assert "UNKNOWN" in refusal
        assert "COMMITTED" not in refusal
        assert "stamped at" not in refusal
        assert f"{_DEPLOY_SCRIPT} {_OLD}" in _flat(refusal)

    def test_a_stamp_the_target_resolves_names_starting_it_again(self, stack):
        """Step 3 committed a stamp the new image wrote: restart first, restore second."""
        result = stack.run(health="unhealthy", **_MIGRATION_BEARING)
        output = result.stdout + result.stderr
        refusal = _refusal(output)
        assert result.returncode == 1, output
        assert stack.pin == _NEW
        assert "Database is now stamped at: 0003" in refusal
        assert f"{_NEW_SHORT}'s step 3 COMMITTED its" in refusal
        restart = f"cd {stack.shekel_dir} && docker compose up -d app"
        assert restart in refusal
        assert "It resolves this stamp, so" in refusal
        assert "pg_restore" in refusal, output
        assert refusal.index(restart) < refusal.index("pg_restore")

    def test_a_stamp_neither_image_resolves_names_only_the_restore(self, stack):
        """A stamp the new image cannot resolve either: no commit claimed, no restart offered.

        Its step 3 cannot have written a revision it has no script for, so
        something other than this deploy moved the stamp.
        """
        result = stack.run(
            health="unhealthy",
            **{**_MIGRATION_BEARING, "stamped_after": "0009"},
        )
        output = result.stdout + result.stderr
        refusal = _refusal(output)
        assert result.returncode == 1, output
        assert "Database is now stamped at: 0009" in refusal
        assert "cannot resolve that revision either" in refusal
        assert "COMMITTED" not in refusal, (
            "a stamp the new image cannot resolve was not written by its step 3"
        )
        assert "It resolves this stamp" not in refusal
        assert f"start {_NEW_SHORT} again" not in refusal
        assert "pg_restore" in refusal

    def test_the_refusal_saves_the_failed_log_and_names_it(self, stack):
        """The log is kept beside the dump, since the restore recreates the container."""
        result = stack.run(
            health="unhealthy", container_log=_REFUSAL_LINE,
            **_MIGRATION_BEARING,
        )
        output = result.stdout + result.stderr
        saved = sorted(stack.backup_dir.glob("*.failed-container.log"))
        assert len(saved) == 1, output
        assert _REFUSAL_LINE in saved[0].read_text(encoding="utf-8")
        assert str(saved[0]) in _refusal(output)
