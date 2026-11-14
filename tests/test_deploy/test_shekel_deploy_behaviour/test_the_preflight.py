"""The pre-flight's classification: a migration-bearing release named, a target older than the
database refused up front, an empty listing refused, and a listing larger than a pipe read whole
by each caller.
"""

from __future__ import annotations

from ._helpers import (
    _OLD,
    _NEW,
    _flat,
    _said,
    _MIGRATION_BEARING,
    _DOWNGRADE,
    _refusal,
    _NEW_SHORT,
    _PAST_A_PIPE,
)


class TestThePreflightIsHonest:
    """The classification itself, since every branch the other parts grade turns on it."""

    def test_it_reports_a_migration_bearing_release(self, stack):
        """The added revisions are named, not merely counted."""
        result = stack.run(health="healthy", **_MIGRATION_BEARING)
        output = result.stdout + result.stderr
        assert "MIGRATION-BEARING release" in output
        assert "0002_b.py" in output and "0003_c.py" in output
        assert "adds these revisions" in output, output
        assert "0001_a.py" not in output.split("adds these revisions")[1][:200]

    def test_dry_run_reports_the_classification_and_changes_nothing(self, stack):
        """``--dry-run`` answers "can this be rolled back?" without deploying."""
        result = stack.run(dry_run=True, health="healthy", **_MIGRATION_BEARING)
        assert result.returncode == 0
        assert "MIGRATION-BEARING release" in result.stdout, (
            "--dry-run does not report whether the release can be rolled back"
        )
        assert stack.pin == _OLD, "--dry-run moved the pin"
        assert stack.dumps == [], "--dry-run took a dump"
        assert not any(
            i.startswith("compose") for i in stack.invocations
        ), "--dry-run started a container"


class TestAListingLargerThanAPipeIsReadWhole:
    """Finding BAL-618, plan step balance:X-dm: a listed revision read as absent.

    ``image_resolves_revisions`` used to ask ``printf '%s\\n' "$listing" |
    grep -q "^${rev}_"`` under ``pipefail``.  ``grep -q`` exits at its first
    match, and printf, if it was still writing, died of SIGPIPE: status 141,
    so a revision on an early line read as ABSENT.  On a fixture of a few
    lines that was a race a loaded CI runner lost now and then.  Findings
    BAL-509 and N-408 recorded this refusal in CI; BAL-618 and N-510 lost
    their output, and their failures match it.  Holding printf between its
    writes reproduced all four on demand.  On a listing larger than the pipe
    plus grep's one read it is no race: printf is still writing when grep
    leaves, every run.  One test per caller, since each asks a safety
    question of its own.
    """

    def test_the_pre_flight_resolves_a_stamp_on_line_one(self, stack):
        """The deploy is not refused up front for a revision the target lists."""
        listing = "0001_a.py\n" + _PAST_A_PIPE
        result = stack.run(
            health="healthy", old_migrations=listing,
            new_migrations=listing, stamped="0001",
        )
        calls = stack.invocations
        assert result.returncode == 0, _said(result, calls)
        assert stack.pin == _NEW, _said(result, calls)

    def test_the_re_pin_decision_resolves_an_unmoved_stamp(self, stack):
        """A failed release that moved no stamp still rolls back on its own."""
        listing = "0001_a.py\n0002_b.py\n" + _PAST_A_PIPE
        result = stack.run(
            health="unhealthy", old_migrations=listing,
            new_migrations=listing, stamped="0002",
        )
        output = result.stdout + result.stderr
        calls = stack.invocations
        assert "REFUSING to roll back" not in output, _said(result, calls)
        assert "rolling back to" in output, _said(result, calls)
        assert stack.pin == _OLD, _said(result, calls)

    def test_the_refusal_resolves_the_stamp_the_target_committed(self, stack):
        """The refusal offers starting the target again, not only the restore."""
        result = stack.run(
            health="unhealthy",
            old_migrations="0001_a.py\n" + _PAST_A_PIPE,
            new_migrations="0001_a.py\n0002_b.py\n0003_c.py\n" + _PAST_A_PIPE,
            stamped="0001", stamped_after="0003",
        )
        refusal = _refusal(result.stdout + result.stderr)
        calls = stack.invocations
        assert stack.pin == _NEW, _said(result, calls)
        assert f"{_NEW_SHORT}'s step 3 COMMITTED its" in refusal, (
            _said(result, calls)
        )
        assert "cannot resolve that revision either" not in refusal, (
            _said(result, calls)
        )


class TestTheDowngradeCaseTheOldDesignCalledSAFE:
    """A target OLDER than the database adds nothing, and cannot boot.

    The defect adversarial review found in the first implementation of R-F8.
    It classified releases by "does the target ADD migrations", which is
    directional: a rollback deploy adds nothing, was therefore called safe,
    took the ordinary path, and produced the two dead containers F-8 is about.
    The question asked now -- "can the target resolve the revision the
    database is stamped at" -- has no direction and catches this before
    anything is written.
    """

    def test_a_target_older_than_the_database_is_refused_up_front(self, stack):
        """It aborts before the dump, the pin, and any container."""
        result = stack.run(health="healthy", **_DOWNGRADE)
        output = result.stdout + result.stderr
        assert result.returncode == 1
        assert (
            "cannot resolve the revision this database is stamped at"
            in _flat(output)
        )
        assert stack.pin == _OLD, "the pin moved for an unbootable target"
        assert stack.dumps == [], "a dump was taken for a refused deploy"
        assert not any(i.startswith("compose") for i in stack.invocations), (
            "a container was started with an image that cannot migrate"
        )

    def test_the_refusal_says_how_a_real_downgrade_is_done(self, stack):
        """It names restore-first rather than leaving the operator stuck."""
        result = stack.run(health="healthy", **_DOWNGRADE)
        output = result.stdout + result.stderr
        assert (
            "restoring a dump taken at or before that revision FIRST"
            in _flat(output)
        )


class TestABrokenProbeIsNeverReadAsSafe:
    """An empty migration listing is a broken probe, not "no migrations".

    ``ls`` in a renamed directory, a changed WORKDIR, a missing ``xargs`` --
    each yields an empty list, and an empty list used to classify the release
    as safe to roll back, silently disabling the whole check.
    """

    def test_an_empty_listing_aborts_rather_than_assuming(self, stack):
        """Zero migrations from an image is refused, loudly."""
        result = stack.run(
            old_migrations="", new_migrations="", stamped="0001",
        )
        output = result.stdout + result.stderr
        assert result.returncode == 1
        assert "ZERO migrations" in output
        assert stack.pin == _OLD
        assert stack.dumps == []
