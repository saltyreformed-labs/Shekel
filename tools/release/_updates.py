"""Whether the Monday update still runs (ruling plan#2351): a release is refused once the
update workflow last published more than :data:`STALE_AFTER` ago, so a script that silently
stopped surfaces at the next release.

**The last update is read, never stored**: the newest run of the update workflow
(:data:`WORKFLOW`) on ``main`` whose ``publish`` job (:data:`JOB`) succeeded, dated by when
GitHub started the run.  A week with nothing to move still runs that job, which publishes
nothing and succeeds, so a quiet week is no stopped update; a run whose refresh or publish
failed, and a day's run of the queue alone, whose publish is skipped, are none.  A commit the
update made would date it too, but a quiet week makes none.

**Read with no credential**: the repository is public, and GitHub serves its Actions runs to
anyone (measured 2026-10-31: answered 200, 60 reads an hour per address), so the read goes
through quill's one HTTP path with no token (:func:`anonymous`).  Made private, the repository
would refuse it, and the release would be judged on nothing (exit 2), never passed.

**Only once production holds the workflow**: GitHub runs a scheduled workflow only from the
default branch, and a dispatched one only when the default branch holds its file
("Scheduled workflows will only run on the default branch"; "This event will only trigger a
workflow run if the workflow file exists on the default branch", GitHub's events page, read
2026-10-31), and ``main`` is the default.  Until production's tree holds the file none can
have run, so the release that brings it is judged without it.  From the next one on, a
release finding no update in the window is refused, saying how to start one.

**The window**: the runs are read newest first, and the walk ends at the first created more
than :data:`STALE_AFTER` ago, since any update older than that refuses the release alike; so a
refusal names the window, not the date of an older update.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from tools.ci.gitcmd import MAIN, GitError, git
from tools.quill.door import GitHub, reading
from tools.release._model import ReleaseError

#: The update workflow, as production's tree holds it, and its file's name, by which GitHub
#: names it.
WORKFLOW = ".github/workflows/updates.yml"
NAME = WORKFLOW.rpartition("/")[2]
#: The job whose success is a Monday update: it opens the week's pull requests, or finds
#: none to open.
JOB = "publish"
#: The branch GitHub runs the workflow from.
BRANCH = MAIN.partition("/")[2]
#: How old the last update may be before a release is refused (ruling plan#2351).
STALE_AFTER = timedelta(days=8)
#: What starts an update run by hand, from ``main``.
START = f"gh workflow run {NAME} --ref {BRANCH}"
#: GitHub's page size for a listing of runs, its largest.
PAGE = 100
#: A GitHub remote's URL, as ``git remote get-url`` prints it over SSH or HTTPS, and the
#: repository it names, ``owner/name``.
_REMOTE = re.compile(r"(?:git@github\.com:|(?:https|ssh)://(?:git@)?github\.com/)"
                     r"(?P<repository>[^/]+/[^/]+?)(?:\.git)?/?")


@dataclass(frozen=True)
class Actions:
    """Where the update workflow's runs are read: through ``github``, from the repository
    ``owner/name``."""

    github: GitHub
    repository: str


@dataclass(frozen=True)
class Published:
    """A Monday update: when GitHub started its run, and the run's page."""

    started: datetime
    run: str


@dataclass(frozen=True)
class Update:
    """The Monday update as a release reads it at ``now``: whether production's tree holds
    the workflow (``held``), and, when it does, its runs' page and the newest update in the
    window (``published``, None when there is none)."""

    held: bool
    now: datetime
    page: str | None = None
    published: Published | None = None

    @property
    def refusals(self) -> tuple[str, ...]:
        """Why the update refuses the release: none, or the one reason it has stopped.  No
        earlier cut answers it, being no cut's."""
        if not self.held:
            return ()
        if self.published is None:
            return (f"no Monday update published from {BRANCH} in the {STALE_AFTER.days} days "
                    f"to {self.now:%Y-%m-%d %H:%M} UTC, so the update has stopped or never "
                    f"started: read why at {self.page}, and `{START}` starts one",)
        return ()


def anonymous(root: Path) -> Actions:
    """The runs read with no credential, from the repository ``root``'s ``origin`` names
    (:func:`repository`)."""
    return Actions(GitHub(None), repository(root))


def repository(root: Path) -> str:
    """``owner/name``, the GitHub repository the checkout at ``root`` fetches as ``origin``.

    Raises:
        ReleaseError: when ``origin`` is no remote, or names no GitHub repository.
    """
    try:
        url = git(root, "remote", "get-url", "origin").strip()
    except GitError as error:
        raise ReleaseError(f"the checkout has no remote origin to read the update workflow's "
                           f"runs from: {error}") from error
    if (named := _REMOTE.fullmatch(url)) is None:
        raise ReleaseError(f"origin is {url!r}, which names no GitHub repository to read the "
                           "update workflow's runs from")
    return named["repository"]


def read(root: Path, base: str, now: datetime,
         actions: Callable[[], Actions] | None) -> Update:
    """The Monday update at ``now``, as production's tree ``base`` and the workflow's runs
    say; no run is read unless ``base`` holds the workflow.

    Raises:
        ReleaseError: when ``base`` holds the workflow and no way to read its runs was given,
            or its repository cannot be named (:func:`repository`).
        tools.quill.door.GitHubError, requests.RequestException: when GitHub cannot be read.
    """
    if not git(root, "ls-tree", "--name-only", base, "--", WORKFLOW).strip():
        return Update(held=False, now=now)
    if actions is None:
        raise ReleaseError(f"production's tree holds {WORKFLOW}, and no way to read its runs "
                           "was given")
    reader = actions()
    page = f"https://github.com/{reader.repository}/actions/workflows/{NAME}"
    return Update(held=True, now=now, page=page,
                  published=_newest(reader, now - STALE_AFTER))


def _newest(reader: Actions, since: datetime) -> Published | None:
    """The newest run on :data:`BRANCH` created at or after ``since`` whose :data:`JOB`
    succeeded; None when there is none."""
    for run in _runs(reader, since):
        with reading(f"the jobs of the update workflow's run {run['id']}"):
            jobs = reader.github.rest(
                "GET", f"/repos/{reader.repository}/actions/runs/{run['id']}/jobs")["jobs"]
        if any(job["name"] == JOB and job["conclusion"] == "success" for job in jobs):
            return Published(datetime.fromisoformat(run["created_at"]), run["html_url"])
    return None


def _runs(reader: Actions, since: datetime) -> Iterator[dict]:
    """The update workflow's runs on :data:`BRANCH`, newest first, as far back as the first
    created before ``since``, which is not yielded."""
    number = 1
    while True:
        with reading(f"the update workflow's runs on {BRANCH}, page {number}"):
            runs = reader.github.rest(
                "GET", f"/repos/{reader.repository}/actions/workflows/{NAME}/runs"
                f"?branch={BRANCH}&per_page={PAGE}&page={number}")["workflow_runs"]
        for run in runs:
            if datetime.fromisoformat(run["created_at"]) < since:
                return
            yield run
        if len(runs) < PAGE:
            return
        number += 1
