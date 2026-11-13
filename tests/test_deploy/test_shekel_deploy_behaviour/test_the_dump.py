"""The pre-deploy dump: no dump, no deploy, and --dump-only takes the deploy's dump and nothing
else.
"""

from __future__ import annotations

import re

import pytest

from ._helpers import (
    _OLD,
    _NEW,
    _flat,
    _said,
    _MIGRATION_BEARING,
    _NO_MIGRATIONS,
    _DUMP_ONLY_CALLS,
    _docker_calls,
    _dump_written_as_part,
)


class TestNoDumpNoDeploy:
    """The database is dumped before the deploy, or the deploy does not run."""

    def test_a_dump_exists_before_compose_is_called(self, stack):
        """When compose runs, a completed dump is already on disk.

        Observed, not inferred: the stub records whether a ``*.dump`` existed
        at the moment ``docker compose`` was invoked.
        """
        result = stack.run(health="healthy", **_NO_MIGRATIONS)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "compose-saw-dump" in stack.invocations, (
            f"docker compose ran with no dump on disk; 'no dump, no deploy' "
            f"is not what the script does.  Invocations: {stack.invocations}"
        )
        assert len(stack.dumps) == 1

    def test_pg_dump_failing_aborts_before_anything_is_touched(self, stack):
        """A dump failure leaves the pin alone and never starts a container."""
        result = stack.run(pgdump_rc="1", **_MIGRATION_BEARING)
        assert result.returncode == 1
        assert "no dump, no deploy" in result.stdout + result.stderr
        assert stack.pin == _OLD, "the pin moved despite the dump failing"
        assert not any(i.startswith("compose") for i in stack.invocations), (
            f"a container was started after the dump failed: "
            f"{stack.invocations}"
        )
        assert stack.dumps == [], "a dump artifact survived a failed pg_dump"

    def test_an_unreadable_dump_is_rejected_and_not_renamed(self, stack):
        """A truncated archive fails ``pg_restore -l`` and aborts the deploy.

        The property the ``.part`` rename exists for: a truncated dump wearing
        a valid name is the artifact the refusal path tells the operator to
        restore from.
        """
        result = stack.run(dump_truncated="1", **_MIGRATION_BEARING)
        assert result.returncode == 1
        assert "truncated" in result.stdout + result.stderr
        assert stack.dumps == [], (
            "an unreadable dump was renamed into place as if it were valid"
        )
        assert list(stack.backup_dir.glob("*.part")) == [], (
            "the rejected .part file was left behind"
        )
        assert stack.pin == _OLD

    def test_the_database_being_down_aborts_the_deploy(self, stack):
        """No reachable database means no dump, so no deploy."""
        result = stack.run(db_running="false", **_MIGRATION_BEARING)
        assert result.returncode == 1
        assert "no dump, no deploy" in result.stdout + result.stderr
        assert stack.pin == _OLD

    def test_a_database_container_docker_cannot_find_aborts_the_deploy(
        self, stack,
    ):
        """docker cannot inspect it at all: not running, so no deploy.

        Plan step balance:X-dm: the running-check is ``container_running``
        in ``scripts/_container_lib.sh`` now (ruling R-BAL254), and a failed
        inspect is its NO, as the replaced ``| grep -q true`` read it.
        """
        result = stack.run(db_inspect_rc="1", **_MIGRATION_BEARING)
        calls = stack.invocations
        assert result.returncode == 1, _said(result, calls)
        assert (
            "database container 'probe-db' is not running; no dump, no deploy"
            in result.stdout + result.stderr
        ), _said(result, calls)
        assert stack.pin == _OLD, _said(result, calls)
        assert stack.dumps == [], _said(result, calls)
        assert not any(c.startswith("compose") for c in calls), (
            _said(result, calls)
        )


class TestDumpOnlyTakesTheDeploysDumpAndNothingElse:
    """``--dump-only``: the same-day dump a release rehearsal restores (card X-cx L9).

    The deploy's own dump function, with its ``.part`` file and its decode of
    every data block, run alone: no pull, no cosign, no pre-flight (listing
    an image's migrations starts a container), no pin written, and no
    container started, stopped or recreated.  The dump's path is the last
    line printed, for a caller to read.
    """

    def test_it_dumps_prints_the_path_and_makes_no_other_call(self, stack):
        """One decoded dump named for the current pin, its path last, four calls.

        .env is not rewritten, not even with the same bytes: its inode,
        modification time and change time are unchanged.
        """
        env_before = stack.env_file.read_bytes()
        stat_before = stack.env_file.stat()
        result = stack.run(args=["--dump-only"])
        calls = stack.invocations
        assert result.returncode == 0, _said(result, calls)
        assert _docker_calls(calls) == _DUMP_ONLY_CALLS, _said(result, calls)
        assert len(stack.dumps) == 1, _said(result, calls)
        assert re.fullmatch(
            rf"shekel_prod_dump_{_OLD[7:19]}_\d{{8}}_\d{{6}}\.dump",
            stack.dumps[0].name,
        ), stack.dumps[0].name
        _dump_written_as_part(calls, stack.dumps[0], result)
        assert result.stdout.splitlines()[-1] == str(stack.dumps[0]), (
            _said(result, calls)
        )
        assert list(stack.backup_dir.glob("*.part")) == [], _said(result, calls)
        stat_after = stack.env_file.stat()
        assert stack.env_file.read_bytes() == env_before, (
            f"--dump-only rewrote .env.  {_said(result, calls)}"
        )
        assert (stat_after.st_ino, stat_after.st_mtime_ns,
                stat_after.st_ctime_ns) == (
            stat_before.st_ino, stat_before.st_mtime_ns, stat_before.st_ctime_ns,
        ), f"--dump-only replaced or touched .env.  {_said(result, calls)}"

    def test_a_read_only_env_file_still_dumps(self, stack):
        """It writes no pin, so it needs .env readable, not writable."""
        stack.env_file.chmod(0o400)
        result = stack.run(args=["--dump-only"])
        calls = stack.invocations
        assert result.returncode == 0, _said(result, calls)
        assert len(stack.dumps) == 1, _said(result, calls)

    def test_a_deploy_still_refuses_a_read_only_env_file(self, stack):
        """The control: a deploy writes the pin, so it refuses before any call."""
        stack.env_file.chmod(0o400)
        result = stack.run(health="healthy", **_NO_MIGRATIONS)
        calls = stack.invocations
        assert result.returncode == 1, _said(result, calls)
        assert "not writable by" in result.stderr, _said(result, calls)
        assert calls == [], _said(result, calls)
        assert stack.pin == _OLD, _said(result, calls)

    @pytest.mark.parametrize("failure, says", [
        ({"pgdump_rc": "1"}, "pg_dump exited 1"),
        ({"dump_truncated": "1"}, "does not decode end to end"),
        ({"db_running": "false"}, "is not running; no dump"),
        ({"db_inspect_rc": "1"}, "is not running; no dump"),
    ], ids=["pg_dump-fails", "truncated", "database-down", "database-unknown"])
    def test_a_dump_that_fails_leaves_no_file_and_prints_no_path(
        self, stack, failure, says,
    ):
        """Each dump refusal these tests drive for a deploy refuses here too, with no path."""
        result = stack.run(args=["--dump-only"], **failure)
        calls = stack.invocations
        assert result.returncode == 1, _said(result, calls)
        assert says in _flat(result.stdout + result.stderr), _said(result, calls)
        assert stack.dumps == [], _said(result, calls)
        assert list(stack.backup_dir.glob("*.part")) == [], _said(result, calls)
        # The path line a caller reads; the log may name the dump it tried.
        assert not [
            line for line in result.stdout.splitlines()
            if line.startswith(str(stack.backup_dir))
        ], _said(result, calls)

    @pytest.mark.parametrize("env_text", [
        "OTHER=1\n", "SHEKEL_IMAGE_DIGEST=\n", "SHEKEL_IMAGE_DIGEST=latest\n",
        "SHEKEL_IMAGE_DIGEST=sha256:\n", "SHEKEL_IMAGE_DIGEST=sha256:zz\n",
        f"SHEKEL_IMAGE_DIGEST={_OLD}0\n",
    ], ids=["no-pin-line", "empty-pin", "not-a-digest", "bare-prefix", "not-hex",
            "too-long"])
    def test_a_pin_it_cannot_read_dumps_nothing(self, stack, env_text):
        """The dump is named for the current pin, so no readable pin, no dump."""
        stack.env_file.write_text(env_text, encoding="utf-8")
        result = stack.run(args=["--dump-only"])
        calls = stack.invocations
        assert result.returncode == 1, _said(result, calls)
        assert "SHEKEL_IMAGE_DIGEST" in result.stderr, _said(result, calls)
        assert stack.dumps == [], _said(result, calls)
        assert not [c for c in calls if c.startswith("exec")], (
            _said(result, calls)
        )

    @pytest.mark.parametrize("args", [
        ["--dump-only", "--dry-run"],
        ["--dry-run", "--dump-only"],
        ["--dump-only", "--no-verify"],
        ["--dump-only", _NEW],
        [_NEW, "--dump-only"],
        ["--dump-only", "--dump-only"],
    ])
    def test_it_takes_no_other_argument(self, stack, args):
        """Beside a digest, a dry run or a skipped check it would read as one: refused first."""
        result = stack.run(args=args)
        calls = stack.invocations
        assert result.returncode == 2, _said(result, calls)
        assert "--dump-only takes no other argument" in result.stderr, (
            _said(result, calls)
        )
        assert calls == [], _said(result, calls)
        assert stack.dumps == [], _said(result, calls)

    def test_a_deploy_s_dump_is_still_named_for_its_target(self, stack):
        """The shared function keeps the deploy's own name: predeploy, then the TARGET."""
        result = stack.run(health="healthy", **_NO_MIGRATIONS)
        calls = stack.invocations
        assert result.returncode == 0, _said(result, calls)
        assert len(stack.dumps) == 1, _said(result, calls)
        assert re.fullmatch(
            rf"shekel_prod_predeploy_{_NEW[7:19]}_\d{{8}}_\d{{6}}\.dump",
            stack.dumps[0].name,
        ), stack.dumps[0].name
        _dump_written_as_part(calls, stack.dumps[0], result)
