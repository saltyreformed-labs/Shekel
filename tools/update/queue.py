"""The daily update queue (ruling plan#2449): each open pull request of Dependabot or the update
App put in ``dev``'s merge queue when it moves pinned versions alone, none across a major, at the
head it was judged at.

**It runs main's own code** (``.github/workflows/updates.yml``'s ``queue`` job), holding the
update App's token (ruling plan#2448) and only the ``landing`` group's packages, so the key is
used by code already released and never by a pull request's.

**Which pull requests**: each open one of this repository (none from a fork) opened by
Dependabot or by the App, as GraphQL names a bot: by its slug, ``dependabot``, where REST says
``dependabot[bot]`` (measured on #626, 2026-10-31); the App's is its token's mint's
``app-slug``.

- **Into ``main``**: a Dependabot security update, which ignores the config's
  ``target-branch``, is retargeted to ``dev`` (:func:`_retarget`) and judged on a later run,
  once GitHub has made its trial merge there.
- **Into ``dev``**: judged on its trial merge (``potentialMergeCommit``), fetched by its sha,
  whose second parent must be the head GitHub named, by the landing check's own walk over
  every file it changes (``unversioned_between``, through quill's door; :func:`_judged`); and
  put in the merge queue when the walk finds no reason and every check at that head passed
  (:func:`_enqueue`): ``enqueuePullRequest`` with ``expectedHeadOid``, so GitHub adds it only
  at that head, and the entry records the head it merges (``MergeQueueEntry.headCommit``).
  A draft, one already queued, one whose trial merge conflicts or is not yet made, and one
  whose checks have not all passed are left, each said with its reason, and read again on the
  next day's run.

**Why the queue and not auto-merge** (measured 2026-10-31): GitHub's ``AutoMergeRequest`` holds
no head commit, and its documentation turns auto-merge off on a push only when the pusher has
no write access, so a pull request auto-merge was enabled on would later merge a head this run
never judged.

**A trial merge GitHub has not made** reads ``UNKNOWN`` while GitHub makes it in the
background, which the read itself starts (measured on #626 and #628: a read seconds later held
it); GitHub's documentation directs a re-read after a wait, made up to :data:`REREADS` times,
:data:`REREAD_SECONDS` apart.

Usage, at the root of a checkout whose ``origin`` is this repository, which serves each trial
merge with no credential, being public::

    UPDATE_TOKEN=<token> UPDATE_APP_SLUG=<slug> GITHUB_REPOSITORY=<owner>/<name> \\
        python -m tools.update.queue

It prints a line for each pull request it reads, what it did and why.  Exit status: 0 each one
read and done; 1 a read or a write failed, each other pull request still tried, or a variable
is unset, saying which.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import requests

from tools.ci.arcs import REPO
from tools.ci.gitcmd import MAIN, GitError, git
from tools.quill.door import (
    GitHub,
    GitHubError,
    described,
    paged,
    reading,
    unversioned_between,
)
from tools.update.app import INTO, REPOSITORY_VARIABLE, TOKEN_VARIABLE, unset

#: The variable holding the App's slug, as GraphQL names the bot it acts as.
APP_VARIABLE = "UPDATE_APP_SLUG"
#: Dependabot, as GraphQL names its bot (measured on #626, 2026-10-31).
DEPENDABOT = "dependabot"
#: The branch a security update is moved off.
OFF = MAIN.partition("/")[2]
#: How many times a trial merge GitHub has not made is read again, and how many seconds apart.
REREADS = 6
REREAD_SECONDS = 10

#: GitHub's ``mergeable`` for a trial merge it has made, for one it is still making, and for
#: a head that conflicts with its base.
_MADE, _MAKING, _CONFLICTS = "MERGEABLE", "UNKNOWN", "CONFLICTING"
#: GitHub's ``statusCheckRollup`` state when every check at a commit passed.
_PASSED = "SUCCESS"

#: What the run reads of each pull request.
_FIELDS = """number id headRefName isDraft isCrossRepository isInMergeQueue baseRefName
headRefOid mergeable author { __typename login } potentialMergeCommit { oid }
commits(last: 1) { nodes { commit { oid statusCheckRollup { state } } } }"""
#: A page of the repository's open pull requests.
_OPEN = """query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, first: 100, after: $after) {
      nodes { %s }
      pageInfo { hasNextPage endCursor }
    }
  }
}""" % _FIELDS
#: One pull request, read again.
_ONE = """query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) { pullRequest(number: $number) { %s } }
}""" % _FIELDS
#: A pull request put in its base branch's merge queue only at the head named.
_ENQUEUE = """mutation($id: ID!, $head: GitObjectID!) {
  enqueuePullRequest(input: {pullRequestId: $id, expectedHeadOid: $head}) {
    mergeQueueEntry { position }
  }
}"""


@dataclass(frozen=True)
class Pull:  # pylint: disable=too-many-instance-attributes
    """An open pull request as the run reads it: its ``number`` and GraphQL ``node`` id; its
    head ``branch``; ``bot``, the slug of the bot that opened it, None when no bot did;
    whether it is from a ``fork``, a ``draft``, or ``queued`` already; its ``base`` branch and
    ``head`` commit; GitHub's ``mergeable`` and its trial ``merge`` (None until made); and
    ``checks``, the state of every check at ``head``, None where none is reported there.

    Pylint: too-many-instance-attributes (12/7) -- **twelve because the run reads twelve
    fields of GitHub's pull request**, one each, all read together and re-read together.
    """

    number: int
    node: str
    branch: str
    bot: str | None
    fork: bool
    draft: bool
    queued: bool
    base: str
    head: str
    mergeable: str
    merge: str | None
    checks: str | None


def _pull(node: dict) -> Pull:
    """A pull request as GraphQL's answer to :data:`_FIELDS` describes it."""
    author = node["author"]
    last = node["commits"]["nodes"]
    rollup = (last[0]["commit"]["statusCheckRollup"]
              if last and last[0]["commit"]["oid"] == node["headRefOid"] else None)
    return Pull(
        number=node["number"], node=node["id"], branch=node["headRefName"],
        bot=author["login"] if author and author["__typename"] == "Bot" else None,
        fork=node["isCrossRepository"], draft=node["isDraft"], queued=node["isInMergeQueue"],
        base=node["baseRefName"], head=node["headRefOid"], mergeable=node["mergeable"],
        merge=(node["potentialMergeCommit"] or {}).get("oid"),
        checks=rollup["state"] if rollup else None,
    )


def _open(github: GitHub, repository: str) -> list[Pull]:
    """Every open pull request of ``repository``, page by page."""
    owner, name = repository.split("/", 1)
    nodes, _ = paged(partial(github.graphql, _OPEN, owner=owner, name=name),
                     lambda answer: answer["repository"]["pullRequests"],
                     f"the open pull requests of {repository}")
    return [_pull(node) for node in nodes]


def _read(github: GitHub, repository: str, number: int) -> Pull:
    """Pull request ``number`` of ``repository``, read again."""
    owner, name = repository.split("/", 1)
    with reading(f"pull request #{number} of {repository}"):
        answer = github.graphql(_ONE, owner=owner, name=name, number=number)
    return _pull(answer["repository"]["pullRequest"])


def _made(github: GitHub, repository: str, pull: Pull,
          sleep: Callable[[float], None]) -> Pull:
    """``pull`` once GitHub has made its trial merge: read again up to :data:`REREADS` times,
    :data:`REREAD_SECONDS` apart, while it reads :data:`_MAKING`; as last read."""
    for _ in range(REREADS):
        if pull.mergeable != _MAKING:
            break
        sleep(REREAD_SECONDS)
        pull = _read(github, repository, pull.number)
    return pull


def _judged(root: Path, pull: Pull) -> tuple[str, ...]:
    """Why ``pull``'s trial merge, fetched into the repository at ``root`` by its sha, is no
    change that moves pinned versions alone, none across a major, over every file it changes;
    none when it is one.  A trial merge whose second parent is not ``pull``'s head is no
    merge of the head judged, and says so.

    Raises:
        tools.ci.gitcmd.GitError: when git cannot fetch or read it.
    """
    git(root, "fetch", "--quiet", "--no-tags", "--depth=2", "origin", pull.merge)
    parents = git(root, "rev-parse", f"{pull.merge}^@").split()
    if len(parents) != 2 or parents[1] != pull.head:
        return (f"its trial merge {pull.merge[:12]} is not a merge of its head "
                f"{pull.head[:12]}",)
    return unversioned_between(root, parents[0], pull.merge)


def _retarget(github: GitHub, repository: str, pull: Pull) -> None:
    """Move ``pull``'s base from :data:`OFF` to :data:`INTO`."""
    github.rest("PATCH", f"/repos/{repository}/pulls/{pull.number}", {"base": INTO})


def _enqueue(github: GitHub, pull: Pull) -> None:
    """Put ``pull`` in its base branch's merge queue, only at its head as read."""
    github.graphql(_ENQUEUE, id=pull.node, head=pull.head)


def _unready(pull: Pull) -> str | None:
    """Why the run leaves ``pull``, a bot's pull request not into :data:`OFF`, before it reads
    the trial merge; None when it reads it."""
    if pull.base != INTO:
        return f"it merges into {pull.base}, neither {INTO} nor {OFF}"
    if pull.draft:
        return "a draft"
    if pull.queued:
        return "in the merge queue already"
    return None


def _unmade(pull: Pull) -> str | None:
    """Why ``pull``, read again while GitHub made its trial merge (:func:`_made`), has none to
    judge; None when it has one."""
    if pull.mergeable == _CONFLICTS:
        return f"it conflicts with {INTO}"
    if pull.mergeable != _MADE or pull.merge is None:
        return (f"GitHub had made no trial merge of it after {REREADS} reads "
                f"{REREAD_SECONDS} s apart")
    return None


def queued(github: GitHub, repository: str, root: Path, pull: Pull,
           sleep: Callable[[float], None] = time.sleep) -> str:
    """What the run does with ``pull``, a bot's pull request of ``repository``, judged in the
    checkout at ``root``: its line's text.

    Raises:
        GitHubError, requests.RequestException: when GitHub refuses a read or a write, or
            cannot be reached.
        tools.ci.gitcmd.GitError: as :func:`_judged` raises it.
    """
    if pull.base == OFF:
        _retarget(github, repository, pull)
        return (f"retargeted from {OFF} to {INTO}, and judged on a later run, once GitHub has "
                f"made its trial merge there")
    if why := _unready(pull):
        return f"left: {why}"
    pull = _made(github, repository, pull, sleep)
    if why := _unmade(pull):
        return f"left: {why}"
    if reasons := _judged(root, pull):
        return f"left: it does more than move pinned versions: {'; '.join(reasons)}"
    if pull.checks != _PASSED:
        return (f"left: it moves pinned versions alone, but its checks at {pull.head[:12]} "
                f"are {pull.checks or 'not reported'}")
    _enqueue(github, pull)
    return f"queued at {pull.head[:12]}"


def run(github: GitHub, repository: str, app: str, root: Path,
        sleep: Callable[[float], None] = time.sleep) -> int:
    """Put each pull request of ``repository`` by Dependabot or the App ``app`` that moves
    pinned versions alone in the merge queue (:func:`queued`), printing a line for each;
    exit as the module says."""
    try:
        pulls = _open(github, repository)
    except (GitHubError, requests.RequestException) as error:
        print(f"update queue: nothing was read: {described(error)}", file=sys.stderr)
        return 1
    bots = {DEPENDABOT, app}
    ours = [pull for pull in pulls if pull.bot in bots and not pull.fork]
    failed = 0
    for pull in ours:
        try:
            line = queued(github, repository, root, pull, sleep)
        except (GitHubError, requests.RequestException, GitError) as error:
            print(f"#{pull.number} ({pull.branch}): failed: {described(error)}",
                  file=sys.stderr)
            failed += 1
        else:
            print(f"#{pull.number} ({pull.branch}): {line}")
    print(f"update queue: {len(ours)} of {len(pulls)} open pull requests are by "
          f"{' or '.join(sorted(bots))}; {failed} failed")
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None, root: Path = REPO,
         sleep: Callable[[float], None] = time.sleep) -> int:
    """Run the queue over the repository and with the token the environment names."""
    argparse.ArgumentParser(prog="python -m tools.update.queue",
                            description="The daily update queue (ruling plan#2449).",
                            ).parse_args(argv)
    if missing := unset(TOKEN_VARIABLE, APP_VARIABLE, REPOSITORY_VARIABLE):
        print(f"update queue: {', '.join(missing)} unset, so nothing was read",
              file=sys.stderr)
        return 1
    return run(GitHub(os.environ[TOKEN_VARIABLE]), os.environ[REPOSITORY_VARIABLE],
               os.environ[APP_VARIABLE], root, sleep)


if __name__ == "__main__":
    sys.exit(main())
