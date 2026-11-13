"""The daily update queue (ruling plan#2449): each open pull request of Dependabot or the update
App put in ``dev``'s merge queue when it moves pinned versions alone, none across a major, at the
head it was judged at; and a comment of the App's, mentioning no one, written once on each
that cannot be for each thing it tells (rulings plan#2450, plan#2467, plan#2468, plan#3622).

**It runs main's own code** (``.github/workflows/updates.yml``'s ``queue`` job), holding the
update App's token (ruling plan#2448) and only the ``landing`` group's packages; GitHub hands
the key only to a job of a run on ``main`` (the environment ``update``, ruling plan#2472), so
it is used by code already released and never by a pull request's.

**Which pull requests**: each open one of this repository (none from a fork) opened by
Dependabot or by the App, as GraphQL names a bot: by its slug, ``dependabot``, where REST says
``dependabot[bot]`` (measured on #626, 2026-10-31); the App's is
:data:`tools.update.app.APP`.

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

**What is told** is a comment from the App on the pull request, written once for each thing
it tells (:func:`_told`), which mentions no one (ruling plan#3622): what reaches the developer
is the card ``quill sync`` files for the pull request, or puts it on while one is open for it
(rulings plan#3619, plan#3722, below).  The comments written before plan#3627 open mentioning
him; their markers are read as any other's.

- **removed from the merge queue at its head**: GitHub took it out of the queue (a check
  failed on top of ``dev``, say) after testing the head it has now, which the removal says:
  it names the merge queue's own trial merge, whose second parent is the head tested
  (measured 2026-11-01 on #589 and #592).  A removal that tested another head, as one a push
  causes does (the publish moving a queued ``update/lock``), or that names no merge, is none
  at the new head, which is judged afresh.  It is left until its head changes, a Dependabot
  rebase or the next Monday's publish (ruling plan#2468), never put back daily, and told once
  for that head.
- **waiting for a card**: the walk gives a reason, so a session must take it onto a card.
  It is told once for each thing it waits on (:class:`~tools.quill.door.Reason`), so a new
  major is told once however often its branch is made again on a later ``dev``, and again
  when a later major arrives on the same pull request (ruling plan#2450), a major beside a
  package the lock adds included.  **One case is told once per pull request**: a change the
  Monday refresh prepares alone whose moved requirement uv cannot lock carries
  ``pyproject.toml`` alone, which pins no version, so the walk reads no major in it and it
  waits on that one refusal, whichever major its requirement later moves to.  Such a pull
  request's own checks fail by design (the landing check refuses it with no card), so they
  tell nothing more.
- **red**: it moves pinned versions alone, but a check at its head failed or errored.  It
  is told once for that head and put in the queue on a later run once every check there
  passes (a re-run), or judged afresh at its next head.

Each comment holds a marker for each thing it tells, a digest of it, so nothing a pull
request carries is spelled inside an HTML comment (:func:`tools.update.app.marker`, its one
form).  The App's own comments are the one record of what was told, read here only when there
is something to tell (:func:`_markers`), and read by ``quill sync`` too, which files a card for
each thing told that no card for its pull request holds, or puts it on the card open for that
pull request (rulings plan#3619, plan#3722); a marker in anyone else's comment counts for
nothing.

**Why the queue and not auto-merge** (measured 2026-10-31): GitHub's ``AutoMergeRequest`` holds
no head commit, and its documentation turns auto-merge off on a push only when the pusher has
no write access, so a pull request auto-merge was enabled on would later merge a head this run
never judged.

**A trial merge GitHub has not made** reads ``UNKNOWN`` while GitHub makes it in the
background, which the read itself starts (measured on #626 and #628: a read seconds later held
it); GitHub's documentation directs a re-read after a wait.  The run waits for every such pull
request together (:func:`_made`, ruling plan#2764): each still unmade is read again in rounds,
up to :data:`REREADS` of them :data:`REREAD_SECONDS` apart, so the run sleeps that many times
at most however many there are, beside one read a round of each still unmade, and each is
judged once the rounds end, after the others.

Usage, at the root of a checkout whose ``origin`` is this repository, which serves each trial
merge with no credential, being public::

    UPDATE_TOKEN=<token> GITHUB_REPOSITORY=<owner>/<name> python -m tools.update.queue

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
    Reason,
    described,
    paged,
    reading,
    unversioned_between,
)
from tools.update.app import (
    APP,
    INTO,
    REPOSITORY_VARIABLE,
    TOKEN_VARIABLE,
    marker,
    markers,
    unset,
)

#: Dependabot, as GraphQL names its bot (measured on #626, 2026-10-31).
DEPENDABOT = "dependabot"
#: The branch a security update is moved off.
OFF = MAIN.partition("/")[2]
#: How many rounds the run reads again each trial merge GitHub has not made, and how many
#: seconds apart.
REREADS = 6
REREAD_SECONDS = 10

#: GitHub's ``mergeable`` for a trial merge it has made, for one it is still making, and for
#: a head that conflicts with its base.
_MADE, _MAKING, _CONFLICTS = "MERGEABLE", "UNKNOWN", "CONFLICTING"
#: GitHub's ``statusCheckRollup`` state when every check at a commit passed, and the states
#: when one failed or errored.
_PASSED = "SUCCESS"
_FAILED = frozenset({"FAILURE", "ERROR"})
#: GitHub's name for a removal from the merge queue, whose ``beforeCommit`` is the merge queue's
#: own trial merge, its second parent the head it tested (measured 2026-11-01 on #589 and #592,
#: each removed for ``failed_checks``).
_REMOVED = "RemovedFromMergeQueueEvent"

#: What the run reads of each pull request.
_FIELDS = """number id headRefName isDraft isCrossRepository isInMergeQueue baseRefName
headRefOid mergeable author { __typename login } potentialMergeCommit { oid }
commits(last: 1) { nodes { commit { oid statusCheckRollup { state } } } }
timelineItems(last: 1, itemTypes: [REMOVED_FROM_MERGE_QUEUE_EVENT]) { nodes { __typename
... on RemovedFromMergeQueueEvent { reason beforeCommit { parents(first: 2) { nodes { oid } }
} } } }"""
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
#: A page of one pull request's comments, each with its author.
_COMMENTS = """query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      comments(first: 100, after: $after) {
        nodes { author { login } body }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}"""
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
    ``head`` commit; GitHub's ``mergeable`` and its trial ``merge`` (None until made);
    ``checks``, the state of every check at ``head``, None where none is reported there; and
    ``removed``, why GitHub removed it from the merge queue as GitHub says it, where its last
    removal tested ``head`` (:data:`_REMOVED`), None where none did.

    Pylint: too-many-instance-attributes (13/7) -- **thirteen because the run reads thirteen
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
    removed: str | None


def _tested(removal: dict) -> str | None:
    """The head the merge queue tested before ``removal``, a removal from it as GraphQL
    answers :data:`_FIELDS`: its trial merge's second parent; None where it names no merge of
    two parents."""
    merge = removal.get("beforeCommit")
    parents = [parent["oid"] for parent in merge["parents"]["nodes"]] if merge else []
    return parents[1] if len(parents) == 2 else None


def _pull(node: dict) -> Pull:
    """A pull request as GraphQL's answer to :data:`_FIELDS` describes it."""
    author = node["author"]
    last = node["commits"]["nodes"]
    rollup = (last[0]["commit"]["statusCheckRollup"]
              if last and last[0]["commit"]["oid"] == node["headRefOid"] else None)
    latest = node["timelineItems"]["nodes"]
    removal = latest[0] if latest and latest[0]["__typename"] == _REMOVED else None
    return Pull(
        number=node["number"], node=node["id"], branch=node["headRefName"],
        bot=author["login"] if author and author["__typename"] == "Bot" else None,
        fork=node["isCrossRepository"], draft=node["isDraft"], queued=node["isInMergeQueue"],
        base=node["baseRefName"], head=node["headRefOid"], mergeable=node["mergeable"],
        merge=(node["potentialMergeCommit"] or {}).get("oid"),
        checks=rollup["state"] if rollup else None,
        removed=(removal["reason"] if removal and _tested(removal) == node["headRefOid"]
                 else None),
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


@dataclass(frozen=True)
class Queue:
    """One run of the queue: ``github``, under the App's token; the ``repository`` it reads;
    ``app``, the App's slug, as GraphQL names the bot that opens its pull requests and writes
    its comments; ``root``, the checkout it judges each trial merge in; and ``sleep``, its wait
    between reads of a trial merge GitHub has not made."""

    github: GitHub
    repository: str
    app: str
    root: Path
    sleep: Callable[[float], None]


def _making(pull: Pull) -> bool:
    """Whether GitHub was still making ``pull``'s trial merge when it was read."""
    return pull.mergeable == _MAKING


def _made(queue: Queue, pulls: Sequence[Pull]) -> tuple[list[Pull], list[tuple[Pull, str]]]:
    """``pulls``, each read while GitHub was making its trial merge, waited for together
    (ruling plan#2764): each still :func:`_making` read again in rounds, up to :data:`REREADS`
    of them :data:`REREAD_SECONDS` apart, so the run sleeps that many times at most however
    many there are.  Each as last read, in order; and each whose read failed, read no more,
    with why."""
    last: list[Pull] = list(pulls)
    unread: list[tuple[Pull, str]] = []
    for _ in range(REREADS):
        if not any(_making(pull) for pull in last):
            break
        queue.sleep(REREAD_SECONDS)
        read = []
        for pull in last:
            if not _making(pull):
                read.append(pull)
                continue
            try:
                read.append(_read(queue.github, queue.repository, pull.number))
            except (GitHubError, requests.RequestException) as error:
                unread.append((pull, described(error)))
        last = read
    return last, unread


def _onto(root: Path, pull: Pull) -> str | None:
    """The commit ``pull``'s trial merge, fetched into the repository at ``root`` by its sha,
    merges its head onto: its first parent; None when it is no merge of the head judged (its
    second parent not ``pull``'s head).

    It is fetched with its two parents alone where that repository is shallow already (the
    workflow's checkout, at depth 1), since a fetch with no depth there pulls each commit of
    ``dev`` the checkout cannot see; and with no depth in a full clone, where a fetch with one
    records each parent as a shallow boundary however much of its history the clone holds,
    cutting every walk across it (measured 2026-11-01: run in a worktree, it turned the
    object store every worktree shares shallow, ``dev`` read as one commit).

    Raises:
        tools.ci.gitcmd.GitError: when git cannot fetch or read it.
    """
    shallow = git(root, "rev-parse", "--is-shallow-repository").strip() == "true"
    git(root, "fetch", "--quiet", "--no-tags", *(["--depth=2"] if shallow else []), "origin",
        pull.merge)
    parents = git(root, "rev-parse", f"{pull.merge}^@").split()
    return parents[0] if len(parents) == 2 and parents[1] == pull.head else None


def _markers(queue: Queue, pull: Pull) -> frozenset[str]:
    """Each marker (:func:`tools.update.app.marker`) in a comment the App wrote on ``pull``,
    read page by page."""
    owner, name = queue.repository.split("/", 1)
    nodes, _ = paged(partial(queue.github.graphql, _COMMENTS, owner=owner, name=name,
                             number=pull.number),
                     lambda answer: answer["repository"]["pullRequest"]["comments"],
                     f"the comments of pull request #{pull.number} of {queue.repository}")
    return frozenset(found for node in nodes
                     if (node["author"] or {}).get("login") == queue.app
                     for found in markers(node["body"]))


def _told(queue: Queue, pull: Pull, told: Sequence[str], text: str) -> str:
    """Comment on ``pull`` saying ``text``, which mentions no one (ruling plan#3622), marked
    with each of ``told`` (:func:`tools.update.app.marker`), unless the App's comments hold
    every one of those marks already: what was done, for the pull request's line."""
    marks = [marker(thing) for thing in told]
    if set(marks) <= _markers(queue, pull):
        return "told in a comment already"
    queue.github.rest("POST", f"/repos/{queue.repository}/issues/{pull.number}/comments",
                      {"body": "\n".join([text, "", *marks]) + "\n"})
    return "told in a comment"


def _removed(queue: Queue, pull: Pull) -> str:
    """Tell once for its head that GitHub removed ``pull`` from the merge queue there, where
    it waits for a new head (ruling plan#2468): its line's text."""
    said = _told(queue, pull, [f"removed {pull.head}"],
                 f"GitHub removed this pull request from the merge queue at "
                 f"{pull.head[:12]} ({pull.removed}). The daily update queue leaves it until "
                 f"its commit changes (a Dependabot rebase, or the next Monday's update); "
                 f"`gh pr merge {pull.number} --merge` puts it back by hand (ruling plan#2468).")
    return (f"left: GitHub removed it from the merge queue at {pull.head[:12]} "
            f"({pull.removed}), so it waits for a new commit; {said}")


def _waiting(queue: Queue, pull: Pull, reasons: Sequence[Reason]) -> str:
    """Tell once for each thing it waits on that ``pull`` waits for a card for ``reasons``
    (ruling plan#2450): its line's text."""
    clauses = "; ".join(reason.clause for reason in reasons)
    said = _told(queue, pull, sorted({f"card {reason.waits_on}" for reason in reasons}),
                 f"This pull request waits for a card: {clauses}. A session reads the "
                 f"changelog, claims a card and takes its change; until then the daily update "
                 f"queue leaves it (rulings plan#2351, plan#2450).")
    return f"left: it does more than move pinned versions: {clauses}; {said}"


def _red(queue: Queue, pull: Pull) -> str:
    """Tell once for its head that ``pull`` moves pinned versions alone but a check failed
    there: its line's text."""
    said = _told(queue, pull, [f"red {pull.head}"],
                 f"This pull request moves pinned versions alone, but its checks at "
                 f"{pull.head[:12]} are {pull.checks}. The daily update queue puts it in the "
                 f"merge queue once every check at that commit passes, and judges it afresh at "
                 f"a new one (ruling plan#2351).")
    return (f"left: it moves pinned versions alone, but its checks at {pull.head[:12]} are "
            f"{pull.checks}; {said}")


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
    """Why ``pull``, as last read (:func:`_made` where GitHub was making its trial merge), has
    no trial merge to judge; None when it has one."""
    if pull.mergeable == _CONFLICTS:
        return f"it conflicts with {INTO}"
    if pull.mergeable != _MADE or pull.merge is None:
        return (f"GitHub had made no trial merge of it after {REREADS} reads "
                f"{REREAD_SECONDS} s apart")
    return None


def _checked(queue: Queue, pull: Pull) -> str:
    """What the run does with ``pull``, whose trial merge moves pinned versions alone, by its
    checks at its head: its line's text."""
    if pull.checks in _FAILED:
        return _red(queue, pull)
    if pull.checks != _PASSED:
        return (f"left: it moves pinned versions alone, but its checks at {pull.head[:12]} "
                f"are {pull.checks or 'not reported'}")
    _enqueue(queue.github, pull)
    return f"queued at {pull.head[:12]}"


def _judged(queue: Queue, pull: Pull) -> str:
    """What the run does with ``pull``, as last read (:func:`_made` where GitHub was making its
    trial merge), by that trial merge: its line's text.

    Raises:
        GitHubError, requests.RequestException: when GitHub refuses a read or a write, or
            cannot be reached.
        tools.ci.gitcmd.GitError: as :func:`_onto` and the walk raise it.
    """
    if why := _unmade(pull):
        return f"left: {why}"
    if (onto := _onto(queue.root, pull)) is None:
        return (f"left: its trial merge {pull.merge[:12]} is not a merge of its head "
                f"{pull.head[:12]}")
    if reasons := unversioned_between(queue.root, onto, pull.merge):
        return _waiting(queue, pull, reasons)
    return _checked(queue, pull)


def queued(queue: Queue, pull: Pull) -> str | None:
    """What ``queue`` does with ``pull``, a bot's pull request of its repository: its line's
    text; None where GitHub is still making its trial merge, which the run waits for beside
    every other such (:func:`_made`) and then judges (:func:`_judged`).

    Raises:
        GitHubError, requests.RequestException: when GitHub refuses a read or a write, or
            cannot be reached.
        tools.ci.gitcmd.GitError: as :func:`_onto` and the walk raise it.
    """
    if pull.base == OFF:
        _retarget(queue.github, queue.repository, pull)
        return (f"retargeted from {OFF} to {INTO}, and judged on a later run, once GitHub has "
                f"made its trial merge there")
    if why := _unready(pull):
        return f"left: {why}"
    if pull.removed is not None:
        return _removed(queue, pull)
    if _making(pull):
        return None
    return _judged(queue, pull)


def _line(pull: Pull, said: str) -> None:
    """Print ``pull``'s line: what the run did with it, and why."""
    print(f"#{pull.number} ({pull.branch}): {said}")


def _failed(pull: Pull, why: str) -> int:
    """Print that a read or a write of ``pull`` failed, and ``why``: one failure, counted."""
    print(f"#{pull.number} ({pull.branch}): failed: {why}", file=sys.stderr)
    return 1


def _tried(pull: Pull, act: Callable[[], str | None]) -> int | None:
    """Do ``act``, what the run does with ``pull``, printing its line: 0 done; 1 where a read or
    a write failed (:func:`_failed`), each other pull request still tried; None where ``act``
    says the run waits for its trial merge (:func:`queued`), printing nothing."""
    try:
        line = act()
    except (GitHubError, requests.RequestException, GitError) as error:
        return _failed(pull, described(error))
    if line is None:
        return None
    _line(pull, line)
    return 0


def _each(queue: Queue, pulls: Sequence[Pull]) -> int:
    """Do with each of ``pulls`` what :func:`queued` says, printing its line, each whose trial
    merge GitHub is making waited for beside every other such (:func:`_made`) and judged after
    them: how many failed, each other still tried."""
    failed, making = 0, []
    for pull in pulls:
        if (done := _tried(pull, partial(queued, queue, pull))) is None:
            making.append(pull)
        else:
            failed += done
    made, unread = _made(queue, making)
    for pull, why in unread:
        failed += _failed(pull, why)
    for pull in made:
        # _judged always says what it did, so it never waits: 0 or 1.
        failed += _tried(pull, partial(_judged, queue, pull)) or 0
    return failed


def run(github: GitHub, repository: str, app: str, root: Path,
        sleep: Callable[[float], None] = time.sleep) -> int:
    """Put each pull request of ``repository`` by Dependabot or the App ``app`` that moves
    pinned versions alone in the merge queue, or tell on it why not (:func:`queued`),
    judged in the checkout at ``root``, printing a line for each; exit as the module says."""
    try:
        pulls = _open(github, repository)
    except (GitHubError, requests.RequestException) as error:
        print(f"update queue: nothing was read: {described(error)}", file=sys.stderr)
        return 1
    bots = {DEPENDABOT, app}
    ours = [pull for pull in pulls if pull.bot in bots and not pull.fork]
    failed = _each(Queue(github, repository, app, root, sleep), ours)
    print(f"update queue: {len(ours)} of {len(pulls)} open pull requests are by "
          f"{' or '.join(sorted(bots))}; {failed} failed")
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None, root: Path = REPO,
         sleep: Callable[[float], None] = time.sleep) -> int:
    """Run the queue over the repository and with the token the environment names, as the App
    :data:`tools.update.app.APP`."""
    argparse.ArgumentParser(prog="python -m tools.update.queue",
                            description="The daily update queue (ruling plan#2449).",
                            ).parse_args(argv)
    if missing := unset(TOKEN_VARIABLE, REPOSITORY_VARIABLE):
        print(f"update queue: {', '.join(missing)} unset, so nothing was read",
              file=sys.stderr)
        return 1
    return run(GitHub(os.environ[TOKEN_VARIABLE]), os.environ[REPOSITORY_VARIABLE], APP, root,
               sleep)


if __name__ == "__main__":
    sys.exit(main())
