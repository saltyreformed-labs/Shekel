"""The Monday update as a release reads it (:mod:`tools.release._updates`, ruling plan#2351):
the newest run on ``main`` whose publish succeeded, dated by its refresh's start, read only
once production's tree holds the workflow, and the refusal when none published in the eight
days to the release."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import yaml

from tools.ci.arcs import REPO
from tools.ci.scratch import run
from tools.release._model import ReleaseError
from tools.release._updates import (
    JOB,
    PAGE,
    REFRESH,
    START,
    STALE_AFTER,
    WORKFLOW,
    Published,
    anonymous,
    read,
    repository,
)
from tools.release.conftest import Runs, daily, monday

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
LISTING = "/repos/o/n/actions/workflows/updates.yml/runs?branch=main&per_page=100&page="
PAGE_LINK = "https://github.com/o/n/actions/workflows/updates.yml"


def _ago(**delta) -> datetime:
    """The instant ``delta`` before :data:`NOW`."""
    return NOW - timedelta(**delta)


def _jobs(number: int) -> str:
    """The path of run ``number``'s jobs."""
    return f"/repos/o/n/actions/runs/{number}/jobs"


def _held(repo) -> str:
    """A base whose tree holds the update workflow."""
    return repo.commit("base", {WORKFLOW: "name: Updates\n"})


def test_production_lacking_the_workflow_reads_no_run_and_refuses_nothing(repo):
    """Before main holds the workflow, GitHub can have run none, so no run is read (no way
    to read one was even given) and the release that brings it is not refused."""
    base = repo.commit("base", {"app.py": "x\n"})
    update = read(repo.root, base, NOW, None)
    assert (update.held, update.published, update.refusals) == (False, None, ())


def test_production_holding_the_workflow_with_no_way_to_read_its_runs_judges_nothing(repo):
    """The runs must be read once main holds the workflow, so no reader is a stop."""
    with pytest.raises(ReleaseError, match="no way to read its runs was given"):
        read(repo.root, _held(repo), NOW, None)


def test_the_update_is_the_newest_run_whose_publish_succeeded(repo):
    """A day's run of the queue alone, whose publish was skipped, is no update; the Monday
    run behind it is, read from main's listing of the update workflow."""
    runs = Runs(daily(3, _ago(days=1)), monday(2, _ago(days=2)), monday(1, _ago(days=9)))
    update = read(repo.root, _held(repo), NOW, runs.actions)
    assert update.published == Published(_ago(days=2), "https://github.com/o/n/actions/runs/2")
    assert (update.page, update.refusals) == (PAGE_LINK, ())
    assert runs.read == [LISTING + "1", _jobs(3), _jobs(2)]


def test_a_quiet_week_is_an_update(repo):
    """A week with nothing to move still runs the publish, which succeeds: no refusal."""
    runs = Runs(monday(1, _ago(days=1)))
    assert not read(repo.root, _held(repo), NOW, runs.actions).refusals


def test_a_failed_monday_run_is_no_update_and_one_before_it_in_the_window_still_is(repo):
    """The newest Monday run's publish failed; last week's, seven days ago, succeeded."""
    runs = Runs(monday(3, _ago(days=1), publish="failure"), daily(2, _ago(days=3)),
                monday(1, _ago(days=7)))
    update = read(repo.root, _held(repo), NOW, runs.actions)
    assert (update.published.started, update.refusals) == (_ago(days=7), ())


def test_a_re_run_repeating_the_refresh_is_dated_by_the_refresh_it_ran(repo):
    """A run created six days ago whose latest attempt, refresh included, ran an hour ago:
    the update is dated by that refresh, not by the run's creation."""
    runs = Runs(monday(1, _ago(days=6), started=_ago(hours=1)))
    update = read(repo.root, _held(repo), NOW, runs.actions)
    assert (update.published.started, update.refusals) == (_ago(hours=1), ())


def test_a_re_run_keeping_the_refresh_is_dated_by_the_refresh_it_kept(repo):
    """A run created six days ago whose latest attempt started an hour ago and repeated the
    publish alone: GitHub lists the refresh it kept, started six days ago, and the update is
    dated by that, not by the attempt's start (``run_started_at``)."""
    runs = Runs(monday(1, _ago(days=6), started=_ago(hours=1), refreshed=_ago(days=6)))
    update = read(repo.root, _held(repo), NOW, runs.actions)
    assert (update.published.started, update.refusals) == (_ago(days=6), ())


def test_a_run_created_before_the_window_is_not_read_though_re_run_since(repo):
    """A run created nine days ago and re-run whole an hour ago ends the walk unread, as the
    listing is by creation: the release is refused, and no jobs of it were asked for."""
    runs = Runs(monday(1, _ago(days=9), started=_ago(hours=1)))
    update = read(repo.root, _held(repo), NOW, runs.actions)
    assert update.published is None and len(update.refusals) == 1
    assert runs.read == [LISTING + "1"]


def test_no_update_in_the_eight_days_refuses_and_says_how_to_start_one(repo):
    """The last successful publish was eight days and a second ago: the walk ends at it,
    never reading its jobs, and the release is refused with where to read why and how to
    start a run."""
    runs = Runs(daily(2, _ago(days=1)), monday(1, _ago(days=8, seconds=1)))
    update = read(repo.root, _held(repo), NOW, runs.actions)
    assert update.published is None
    assert update.refusals == (
        "no Monday update published from main in the 8 days to 2026-11-10 12:00 UTC, so the "
        f"update has stopped or never started: read why at {PAGE_LINK}, and "
        "`gh workflow run updates.yml --ref main` starts one",)
    assert runs.read == [LISTING + "1", _jobs(2)]


def test_an_update_exactly_eight_days_old_is_not_more_than_eight_days_old(repo):
    """The refusal is for an update MORE than eight days old (ruling plan#2351)."""
    runs = Runs(monday(1, NOW - STALE_AFTER))
    assert not read(repo.root, _held(repo), NOW, runs.actions).refusals


def test_the_first_release_after_the_workflow_lands_with_no_run_yet_is_refused(repo):
    """Main holds the workflow and GitHub lists no run of it: refused, naming how to start
    one, never passed by default."""
    update = read(repo.root, _held(repo), NOW, Runs().actions)
    assert len(update.refusals) == 1 and START in update.refusals[0]


def test_a_full_page_of_queue_runs_is_read_past_to_the_next_page(repo):
    """A whole page of runs that are no update, all inside the window, then the update on
    the next page."""
    queued = [daily(1000 + n, _ago(minutes=n + 1)) for n in range(PAGE)]
    runs = Runs(*queued, monday(1, _ago(days=2)))
    update = read(repo.root, _held(repo), NOW, runs.actions)
    assert update.published.started == _ago(days=2)
    assert runs.read[-2:] == [LISTING + "2", _jobs(1)]


@pytest.mark.parametrize("url", [
    "git@github.com:saltyreformed-labs/Shekel.git",
    "https://github.com/saltyreformed-labs/Shekel.git",
    "https://github.com/saltyreformed-labs/Shekel",
    "ssh://git@github.com/saltyreformed-labs/Shekel.git",
])
def test_the_repository_is_the_one_origin_names(repo, url):
    """Each form ``git remote get-url`` prints for a GitHub remote names one repository."""
    run(repo.root, "remote", "add", "origin", url)
    assert repository(repo.root) == "saltyreformed-labs/Shekel"


@pytest.mark.parametrize("url", ["git@gitlab.com:o/n.git", "/srv/git/shekel.git",
                                 "https://github.com/o/n/extra"])
def test_an_origin_naming_no_github_repository_is_refused(repo, url):
    """A remote elsewhere, or a path, names no repository whose runs could be read."""
    run(repo.root, "remote", "add", "origin", url)
    with pytest.raises(ReleaseError, match="names no GitHub repository"):
        repository(repo.root)


def test_a_checkout_with_no_origin_is_refused(repo):
    """No remote at all is a stop, never a guess at the repository."""
    with pytest.raises(ReleaseError, match="no remote origin"):
        repository(repo.root)


def test_the_anonymous_read_is_of_origin_s_repository(repo):
    """The command line's reader names origin's repository."""
    run(repo.root, "remote", "add", "origin", "git@github.com:o/n.git")
    assert anonymous(repo.root).repository == "o/n"


def test_the_jobs_read_are_the_workflow_s_publish_and_the_refresh_it_needs_by_their_ids():
    """GitHub names a job by its ``name:``, else by its id: the two jobs this module reads
    are jobs of the committed workflow under those ids, with no ``name:`` to rename them, or
    every release would find no update and be refused; and the publish needs the refresh,
    so GitHub lists the refresh beside every publish that succeeded."""
    jobs = yaml.safe_load((REPO / WORKFLOW).read_text(encoding="utf-8"))["jobs"]
    assert all(name in jobs and "name" not in jobs[name] for name in (JOB, REFRESH))
    assert jobs[JOB]["needs"] == REFRESH
