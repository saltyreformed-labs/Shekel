"""The daily update queue (:mod:`tools.update.queue`, ruling plan#2449): each bot's pull request
put in the merge queue only when its trial merge moves pinned versions alone, at the head judged;
and the developer mentioned once on each that cannot be (rulings plan#2450, plan#2467,
plan#2468).

GitHub's API is faked (:class:`_GitHub`) over recorded pull requests, answering as GitHub did
when read on 2026-10-31 (#626, #628) and 2026-11-01: a bot named by its slug, a comment's
author too (``shekel-quill``, on the tracker), a trial merge ``UNKNOWN`` until made, and the
last of a pull request's queue removals, commits and force pushes as its timeline holds them
(#589, #592, #628).  Git is real: each pull request's head and trial merge are made in a
second clone and pushed to a bare ``origin`` as ``refs/pull/N/merge``, as GitHub serves them,
so the queue fetches each by its sha into a checkout that held neither.
"""
from __future__ import annotations

import hashlib
import json
import re

import pytest

from tools.ci import gitcmd
from tools.ci.scratch import commit, commit_files, elsewhere, repository, run, with_origin
from tools.quill.door import LOCK, GitHub
from tools.update import queue
from tools.update.queue import DEPENDABOT, REREAD_SECONDS, REREADS

#: The update App's slug, as its token's mint gives it.
_APP = "shekel-updates"
#: The repository the queue reads.
_REPOSITORY = "owner/shekel"
#: GitHub's GraphQL endpoint.
_GRAPHQL = "https://api.github.com/graphql"
#: A commit no repository holds: a fetch of it fails, so a test that reaches git with one fails.
_NOWHERE = "f" * 40


def _lock(version):
    """A lock holding pillow at ``version``, its one file on PyPI named for its release."""
    return ('version = 1\nrevision = 3\nrequires-python = ">=3.14"\n\n[[package]]\n'
            f'name = "pillow"\nversion = "{version}"\n'
            'source = { registry = "https://pypi.org/simple" }\n'
            'sdist = { url = "https://files.pythonhosted.org/packages/'
            f'pillow-{version}.tar.gz", hash = "sha256:{"0" * 64}" }}\n')


def _input(query, variables):
    """What the mutation ``query`` binds in its ``input``, each field to its value: a variable
    read from ``variables``, so a field the mutation does not bind is absent, whatever
    variables it sends beside it."""
    bound = re.search(r"\(input: \{([^}]*)\}\)", query)[1]
    return {field: variables[value.removeprefix("$")]
            for field, value in (pair.split(": ") for pair in bound.split(", "))}


class _Response:
    """The slice of ``requests.Response`` quill's client reads."""

    def __init__(self, status_code, body):
        """One answer: its status and JSON body."""
        self.status_code = status_code
        self.content = json.dumps(body).encode()
        self.text = self.content.decode()
        self.headers = {}
        self._body = body

    def json(self):
        """The body."""
        return self._body


class _GitHub:  # pylint: disable=too-many-instance-attributes
    """GitHub's API as the queue uses it, over recorded pull requests (each a node as
    GraphQL answers :data:`queue._FIELDS`): the open ones ``page`` at a time, each one read
    again from ``rereads`` (by number, its later states in turn), each one's ``comments``
    (by number, each a node as GraphQL answers :data:`queue._COMMENTS`; none until a test
    gives some, a comment written here added as the App's) ``page`` at a time too, each
    read of them recorded in ``reads``, and each write in ``writes``, an enqueue or a
    comment on a number in ``refuse`` refused as GitHub refuses one.

    Pylint: too-many-instance-attributes (8/7) -- **eight because the fake answers four
    reads and two writes**, each held as GitHub would hold it, plus the page size and the
    records of reads and writes.
    """

    def __init__(self, pulls, *, page=100, rereads=None, refuse=(), listing=200):
        """Hold the pull requests, their later states, and what GitHub refuses."""
        self.pulls = pulls
        self.page = page
        self.rereads = {number: list(states) for number, states in (rereads or {}).items()}
        self.refuse = set(refuse)
        self.listing = listing
        self.comments = {}
        self.writes = []
        self.reads = []

    def _comment(self, url, body):
        """Write a comment as the App on the pull request ``url`` names, or refuse it."""
        number = int(url.rsplit("/", 2)[1])
        self.writes.append(("comment", number, body["body"]))
        if number in self.refuse:
            return _Response(403, {"message": "Resource not accessible by integration"})
        self.comments.setdefault(number, []).append({"author": {"login": _APP},
                                                     "body": body["body"]})
        return _Response(201, {"id": len(self.writes)})

    def _comments_page(self, variables):
        """A page of one pull request's comments, as :data:`queue._COMMENTS` reads it."""
        self.reads.append(("comments", variables["number"], variables["after"]))
        nodes = self.comments.get(variables["number"], [])
        start = int(variables["after"] or 0)
        end = start + self.page
        return _Response(200, {"data": {"repository": {"pullRequest": {"comments": {
            "nodes": nodes[start:end],
            "pageInfo": {"hasNextPage": end < len(nodes), "endCursor": str(end)}}}}}})

    def request(self, method, url, **kwargs):
        """Answer one request as GitHub would."""
        body = kwargs["json"]
        if method == "PATCH":
            self.writes.append(("PATCH", url, body))
            return _Response(200, {"number": int(url.rsplit("/", 1)[1])})
        if url.endswith("/comments"):
            assert method == "POST"
            return self._comment(url, body)
        assert (method, url) == ("POST", _GRAPHQL)
        return self._graphql(body["query"], body["variables"])

    def _graphql(self, query, variables):
        """Answer one GraphQL ``query`` as GitHub would."""
        if "comments(" in query:
            return self._comments_page(variables)
        if "enqueuePullRequest" in query:
            fields = _input(query, variables)
            self.writes.append(("enqueue", fields["pullRequestId"], fields.get("expectedHeadOid")))
            if int(fields["pullRequestId"].removeprefix("PR_")) in self.refuse:
                return _Response(200, {"errors": [{"message": "Pull request is not mergeable"}]})
            return _Response(200, {"data": {"enqueuePullRequest": {
                "mergeQueueEntry": {"position": 1}}}})
        if "pullRequests(" in query:
            if self.listing != 200:
                return _Response(self.listing, {"message": "Bad credentials"})
            start = int(variables["after"] or 0)
            end = start + self.page
            return _Response(200, {"data": {"repository": {"pullRequests": {
                "nodes": self.pulls[start:end],
                "pageInfo": {"hasNextPage": end < len(self.pulls), "endCursor": str(end)}}}}})
        return _Response(200, {"data": {"repository": {
            "pullRequest": self.rereads[variables["number"]].pop(0)}}})


def _by(login, kind="Bot"):
    """The author GraphQL names: an actor of ``kind`` called ``login``."""
    return {"__typename": kind, "login": login}


def _checks(state, at):
    """The pull request's last commit ``at``, with every check's state ``state`` (None when
    none is reported)."""
    return {"nodes": [{"commit": {"oid": at,
                                  "statusCheckRollup": {"state": state} if state else None}}]}


def _latest(kind, reason=None):
    """The last of a pull request's queue removals, commits and force pushes, as its timeline
    holds it: of ``kind``, GitHub's name for it, a removal's ``reason`` with it."""
    return {"nodes": [{"__typename": kind} | ({"reason": reason} if reason else {})]}


def _node(number, head, merge, **fields):
    """A pull request as GraphQL answers :data:`queue._FIELDS`: Dependabot's into ``dev``, its
    head ``head``, its trial merge ``merge`` (None while not made), every check at its head
    passed and its last timeline item of those the queue reads its first commit, but for each
    of ``fields``, by GraphQL's name."""
    return {
        "number": number, "id": f"PR_{number}", "headRefName": f"update/{number}",
        "isDraft": False, "isCrossRepository": False, "isInMergeQueue": False,
        "baseRefName": "dev", "headRefOid": head,
        "mergeable": "MERGEABLE" if merge else "UNKNOWN", "author": _by(DEPENDABOT),
        "potentialMergeCommit": {"oid": merge} if merge else None,
        "commits": _checks("SUCCESS", head),
        "timelineItems": _latest("PullRequestCommit"),
    } | fields


def _mentions(fake, number):
    """The bodies of the comments written on pull request ``number``, in order."""
    return [body for kind, at, body in fake.writes if (kind, at) == ("comment", number)]


def _marked(told):
    """The marker naming ``told`` in a comment of the App's, spelled here as well as in the
    queue: a marker the App wrote stays on GitHub across releases, so a change to its form
    must fail a test, never silently forget what each comment told."""
    return f"<!-- update: mention {hashlib.sha256(told.encode()).hexdigest()} -->"


@pytest.fixture(name="world")
def _world(tmp_path):
    """The checkout the queue runs in, ``dev`` holding the lock at pillow 12.3.0 beside
    ``app/x.py``, and a bare ``origin``; with :meth:`_World.pull` to push a pull request's
    head and trial merge there from another clone."""
    work = repository(tmp_path, "work")
    base = commit_files(work, commit(work, "start"), "dev",
                        {LOCK: _lock("12.3.0"), "app/x.py": "a\n"})
    with_origin(work, base)
    return _World(work, base)


class _World:
    """The queue's checkout, ``dev``'s commit, and a clone that makes pull requests."""

    def __init__(self, work, base):
        """Hold the checkout and ``dev``'s commit; the other clone is made on first use."""
        self.work = work
        self.base = base

    def pull(self, number, files):
        """Pull request ``number`` writing ``files`` on ``dev``: its head and trial merge, made
        in another clone and pushed to ``origin`` as GitHub serves them, never fetched here."""
        other = elsewhere(self.work)
        head = commit_files(other, self.base, f"pull {number}", files)
        tree = run(other, "rev-parse", f"{head}^{{tree}}")
        merge = run(other, "commit-tree", tree, "-p", self.base, "-p", head, "-m", "Merge")
        run(other, "push", "--quiet", "origin", f"{head}:refs/pull/{number}/head",
            f"{merge}:refs/pull/{number}/merge")
        assert gitcmd.run(self.work, "cat-file", "-e", merge).returncode != 0
        return head, merge


def _run(world, fake, sleep=None):
    """The queue over ``fake`` in ``world``'s checkout; its exit status and the sleeps."""
    slept = []
    status = queue.run(GitHub("token", session=fake), _REPOSITORY, _APP, world.work,
                       sleep or slept.append)
    return status, slept


def test_a_version_only_pull_request_whose_checks_passed_is_queued_at_the_head_judged(
        world, capsys):
    """A minor move of the lock, every check passed: fetched by its trial merge's sha into a
    checkout that held neither, judged, and enqueued only at the head GitHub named."""
    head, merge = world.pull(7, {LOCK: _lock("12.4.1")})
    fake = _GitHub([_node(7, head, merge)])
    assert _run(world, fake) == (0, [])
    assert fake.writes == [("enqueue", "PR_7", head)]
    assert f"#7 (update/7): queued at {head[:12]}" in capsys.readouterr().out


@pytest.mark.parametrize(("files", "why"), [
    ({LOCK: _lock("13.0.0")}, "uv.lock moves pillow from 12.3.0 to 13.0.0, a new major version"),
    ({"app/x.py": "b\n"}, "app/x.py pins no version"),
], ids=["a new major", "a file that pins nothing"])
def test_a_change_that_does_more_than_move_versions_is_left_saying_why(world, capsys, files,
                                                                      why):
    """A trial merge the landing check's walk finds a reason in is never enqueued, green or
    not: the developer is mentioned that it waits for a card, and its line names the walk's
    reason."""
    head, merge = world.pull(7, files)
    fake = _GitHub([_node(7, head, merge)])
    assert _run(world, fake) == (0, [])
    assert [kind for kind, *_ in fake.writes] == ["comment"]
    assert _mentions(fake, 7)[0].startswith(
        f"@SaltyReformed This pull request waits for a card: {why}. ")
    assert (f"#7 (update/7): left: it does more than move pinned versions: {why}; the "
            "developer mentioned\n") in capsys.readouterr().out


@pytest.mark.parametrize(("checks", "checked", "said"), [
    ("PENDING", None, "PENDING"), ("EXPECTED", None, "EXPECTED"),
    (None, None, "not reported"), ("SUCCESS", "a" * 40, "not reported"),
    ("FAILURE", "a" * 40, "not reported"),
], ids=["running", "a required check not yet reported", "none reported",
        "passed at another commit", "failed at another commit"])
def test_a_version_only_change_whose_checks_have_not_all_passed_at_its_head_is_left(
        world, capsys, checks, checked, said):
    """Version-only but not green at the head judged, and not red there either: left for the
    next day's run, its checks' state said, no one mentioned; a read at any commit but its
    head is no read at it."""
    head, merge = world.pull(7, {LOCK: _lock("12.4.1")})
    fake = _GitHub([_node(7, head, merge, commits=_checks(checks, checked or head))])
    assert _run(world, fake) == (0, [])
    assert not fake.writes
    assert (f"left: it moves pinned versions alone, but its checks at {head[:12]} are {said}"
            in capsys.readouterr().out)


def test_a_security_update_into_main_is_retargeted_to_dev_and_judged_on_a_later_run(
        world, capsys):
    """Into ``main``: its base moved to ``dev``, nothing fetched or judged (its trial merge is
    ``main``'s, a commit no repository holds here) and nothing enqueued."""
    fake = _GitHub([_node(9, _NOWHERE, _NOWHERE, baseRefName="main")])
    assert _run(world, fake) == (0, [])
    assert fake.writes == [("PATCH", "https://api.github.com/repos/owner/shekel/pulls/9",
                            {"base": "dev"})]
    assert "#9 (update/9): retargeted from main to dev" in capsys.readouterr().out


def test_only_pull_requests_of_this_repository_by_dependabot_or_the_app_are_read(world, capsys):
    """Over a listing read two to a page: the App's pull request is queued as Dependabot's
    would be, and a person's, another bot's, a fork's and a deleted account's are never
    judged (each names a commit no repository holds, so a fetch of it fails the run)."""
    head, merge = world.pull(7, {LOCK: _lock("12.4.1")})
    fake = _GitHub([
        _node(3, _NOWHERE, _NOWHERE, author=_by("SaltyReformed", "User")),
        _node(4, _NOWHERE, _NOWHERE, author=_by("renovate")),
        _node(5, _NOWHERE, _NOWHERE, isCrossRepository=True),
        _node(6, _NOWHERE, _NOWHERE, author=None),
        _node(7, head, merge, author=_by(_APP)),
    ], page=2)
    assert _run(world, fake) == (0, [])
    assert fake.writes == [("enqueue", "PR_7", head)]
    assert capsys.readouterr().out.splitlines() == [
        f"#7 (update/7): queued at {head[:12]}",
        "update queue: 1 of 5 open pull requests are by dependabot or shekel-updates; "
        "0 failed"]


def test_a_trial_merge_github_is_still_making_is_read_again_until_made(world):
    """``UNKNOWN`` is GitHub making the trial merge: read again after a wait, until made."""
    head, merge = world.pull(7, {LOCK: _lock("12.4.1")})
    making = _node(7, head, None)
    fake = _GitHub([making], rereads={7: [making, _node(7, head, merge)]})
    assert _run(world, fake) == (0, [REREAD_SECONDS, REREAD_SECONDS])
    assert fake.writes == [("enqueue", "PR_7", head)]


def test_a_trial_merge_never_made_is_left_after_the_rereads(world, capsys):
    """One still ``UNKNOWN`` after :data:`REREADS` reads again is left, nothing judged."""
    making = _node(7, _NOWHERE, None)
    fake = _GitHub([making], rereads={7: [making] * REREADS})
    assert _run(world, fake) == (0, [REREAD_SECONDS] * REREADS)
    assert not fake.writes
    assert (f"left: GitHub had made no trial merge of it after {REREADS} reads "
            f"{REREAD_SECONDS} s apart") in capsys.readouterr().out


@pytest.mark.parametrize(("node", "said"), [
    (_node(7, _NOWHERE, _NOWHERE, isDraft=True), "left: a draft"),
    (_node(7, _NOWHERE, _NOWHERE, isInMergeQueue=True), "left: in the merge queue already"),
    (_node(7, _NOWHERE, None, mergeable="CONFLICTING"), "left: it conflicts with dev"),
    (_node(7, _NOWHERE, _NOWHERE, baseRefName="release"),
     "left: it merges into release, neither dev nor main"),
], ids=["a draft", "queued already", "a conflict", "another base"])
def test_a_pull_request_the_queue_cannot_take_is_left_unjudged(world, capsys, node, said):
    """Nothing is fetched (each names a commit no repository holds) or written."""
    fake = _GitHub([node])
    assert _run(world, fake) == (0, [])
    assert not fake.writes
    assert f"#7 (update/7): {said}\n" in capsys.readouterr().out


def test_a_trial_merge_that_is_not_of_the_head_github_named_is_left(world, capsys):
    """GitHub names one head, its trial merge merges another: no merge of the head judged,
    so nothing is judged of it and no one is told it waits for a card."""
    _, merge = world.pull(7, {LOCK: _lock("13.0.0")})
    fake = _GitHub([_node(7, "a" * 40, merge)])
    assert _run(world, fake) == (0, [])
    assert not fake.writes
    assert (f"#7 (update/7): left: its trial merge {merge[:12]} is not a merge of its head "
            "aaaaaaaaaaaa\n") in capsys.readouterr().out


def test_a_refused_write_fails_the_run_once_every_other_pull_request_is_tried(world, capsys):
    """GitHub refuses #7's enqueue: its line says so, #8 is still queued, and the run fails."""
    first, first_merge = world.pull(7, {LOCK: _lock("12.4.1")})
    second, second_merge = world.pull(8, {LOCK: _lock("12.3.1")})
    fake = _GitHub([_node(7, first, first_merge), _node(8, second, second_merge)], refuse={7})
    assert _run(world, fake) == (1, [])
    assert fake.writes == [("enqueue", "PR_7", first), ("enqueue", "PR_8", second)]
    out, err = capsys.readouterr()
    assert "#7 (update/7): failed: graphql:" in err
    assert "Pull request is not mergeable" in err
    assert f"#8 (update/8): queued at {second[:12]}" in out


def test_a_listing_github_refuses_fails_the_run_reading_nothing(world, capsys):
    """The open pull requests cannot be read: the run fails, naming the read."""
    fake = _GitHub([], listing=401)
    assert _run(world, fake) == (1, [])
    err = capsys.readouterr().err
    assert err.startswith("update queue: nothing was read: ")
    assert "while reading the open pull requests of owner/shekel, page 1" in err


def test_the_queue_starts_only_with_each_of_its_variables(monkeypatch, capsys, world):
    """An unset or empty variable stops the run before any read, naming each one."""
    monkeypatch.setenv(queue.TOKEN_VARIABLE, "token")
    monkeypatch.setenv(queue.APP_VARIABLE, "")
    monkeypatch.delenv(queue.REPOSITORY_VARIABLE, raising=False)
    assert queue.main([], root=world.work) == 1
    assert capsys.readouterr().err == (
        "update queue: UPDATE_APP_SLUG, GITHUB_REPOSITORY unset, so nothing was read\n")


# -- what reaches the developer (rulings plan#2450, plan#2467, plan#2468) ------------------

@pytest.mark.parametrize("checks", ["FAILURE", "ERROR"])
def test_a_red_version_only_change_is_left_and_mentioned_once_for_its_head(world, capsys,
                                                                           checks):
    """Version-only, a check failed or errored at its head: left, the developer mentioned in
    a comment marked for that head; the next day's run at that head mentions no one again,
    and a new head, red too, is mentioned afresh."""
    head, merge = world.pull(7, {LOCK: _lock("12.4.1")})
    fake = _GitHub([_node(7, head, merge, commits=_checks(checks, head))])
    assert _run(world, fake) == (0, [])
    (body,) = _mentions(fake, 7)
    assert body == (
        f"@SaltyReformed This pull request moves pinned versions alone, but its checks at "
        f"{head[:12]} are {checks}. The daily update queue puts it in the merge queue once "
        f"every check at that commit passes, and judges it afresh at a new one (ruling "
        f"plan#2351).\n\n{_marked(f'red {head}')}\n")
    assert _run(world, fake) == (0, [])
    assert len(_mentions(fake, 7)) == 1
    later, later_merge = world.pull(17, {LOCK: _lock("12.4.2")})
    fake.pulls = [_node(7, later, later_merge, commits=_checks(checks, later))]
    assert _run(world, fake) == (0, [])
    assert [kind for kind, *_ in fake.writes] == ["comment", "comment"]
    out = capsys.readouterr().out
    left = f"left: it moves pinned versions alone, but its checks at {{}} are {checks}; "
    assert out.count(left.format(head[:12]) + "the developer mentioned\n") == 1
    assert out.count(left.format(head[:12]) + "the developer was mentioned already\n") == 1
    assert out.count(left.format(later[:12]) + "the developer mentioned\n") == 1


def test_a_red_change_that_turns_green_at_its_head_is_queued_with_no_more_mention(world):
    """A re-run passes at the head mentioned: the next day's run queues it there, and reads
    no comment, as nothing is to be told."""
    head, merge = world.pull(7, {LOCK: _lock("12.4.1")})
    fake = _GitHub([_node(7, head, merge, commits=_checks("FAILURE", head))])
    _run(world, fake)
    fake.pulls, fake.reads = [_node(7, head, merge)], []
    assert _run(world, fake) == (0, [])
    assert [kind for kind, *_ in fake.writes] == ["comment", "enqueue"]
    assert not fake.reads


def test_a_pull_request_removed_from_the_merge_queue_at_its_head_waits_for_a_new_one(
        world, capsys):
    """GitHub removed it from the queue and no commit reached it since: left with no trial
    merge read (each names a commit no repository holds) though its own checks passed, and
    mentioned once for that head, GitHub's reason said; the next day's run writes nothing."""
    fake = _GitHub([_node(7, _NOWHERE, _NOWHERE,
                          timelineItems=_latest("RemovedFromMergeQueueEvent", "failed_checks"))])
    assert _run(world, fake) == (0, [])
    assert _run(world, fake) == (0, [])
    (body,) = _mentions(fake, 7)
    assert body == (
        "@SaltyReformed GitHub removed this pull request from the merge queue at ffffffffffff "
        "(failed_checks). The daily update queue leaves it until its commit changes (a "
        "Dependabot rebase, or the next Monday's update); `gh pr merge 7 --merge` puts it back "
        f"by hand (ruling plan#2468).\n\n{_marked(f'removed {_NOWHERE}')}\n")
    assert [kind for kind, *_ in fake.writes] == ["comment"]
    left = ("#7 (update/7): left: GitHub removed it from the merge queue at ffffffffffff "
            "(failed_checks), so it waits for a new commit; the developer ")
    assert capsys.readouterr().out.count(left) == 2


@pytest.mark.parametrize("since", ["PullRequestCommit", "HeadRefForcePushedEvent"])
def test_a_pull_request_reached_by_a_commit_since_its_removal_is_judged_again(world, since):
    """A commit or a force push after the removal is a new head: judged, and queued when it
    moves pinned versions alone and its checks passed, no one mentioned."""
    head, merge = world.pull(7, {LOCK: _lock("12.4.1")})
    fake = _GitHub([_node(7, head, merge, timelineItems=_latest(since))])
    assert _run(world, fake) == (0, [])
    assert fake.writes == [("enqueue", "PR_7", head)]


def test_a_change_waiting_for_a_card_is_mentioned_once_for_each_major_it_enters(world,
                                                                                capsys):
    """A new major is told once: not again when the branch is made again at a new head moving
    the same major further, and afresh when a later major arrives on the same pull request,
    each comment naming every reason, marked for each thing it waits on."""
    fake = _GitHub([])
    for number, version in ((7, "13.0.0"), (17, "13.1.0"), (27, "14.0.0")):
        head, merge = world.pull(number, {LOCK: _lock(version)})
        fake.pulls = [_node(7, head, merge)]
        assert _run(world, fake) == (0, [])
    first, later = _mentions(fake, 7)
    assert first.endswith(f"\n\n{_marked('card pillow 13')}\n")
    assert later == (
        "@SaltyReformed This pull request waits for a card: uv.lock moves pillow from 12.3.0 "
        "to 14.0.0, a new major version. A session reads the changelog, claims a card and "
        "takes its change; until then the daily update queue leaves it (rulings plan#2351, "
        f"plan#2450).\n\n{_marked('card pillow 14')}\n")
    assert ("pillow from 12.3.0 to 13.1.0, a new major version; the developer was mentioned "
            "already\n") in capsys.readouterr().out


def test_only_the_app_s_own_comments_record_a_mention(world):
    """The marker for the head, in a comment by a person and by Dependabot, counts for
    nothing: the App mentions the developer; its own comment, on the second page of the
    comments, is found."""
    head, merge = world.pull(7, {LOCK: _lock("12.4.1")})
    marked = _marked(f"red {head}")
    fake = _GitHub([_node(7, head, merge, commits=_checks("FAILURE", head))], page=2)
    fake.comments[7] = [{"author": {"login": "SaltyReformed"}, "body": marked},
                        {"author": None, "body": marked},
                        {"author": {"login": DEPENDABOT}, "body": marked}]
    assert _run(world, fake) == (0, [])
    assert len(_mentions(fake, 7)) == 1
    assert _run(world, fake) == (0, [])
    assert len(_mentions(fake, 7)) == 1
    assert [read for read in fake.reads if read[0] == "comments"][-2:] == [
        ("comments", 7, None), ("comments", 7, "2")]


def test_a_refused_mention_fails_the_run_once_every_other_pull_request_is_tried(world,
                                                                                capsys):
    """GitHub refuses #7's comment: its line says so, #8 is still queued, and the run fails,
    which the workflow's alert reports."""
    first, first_merge = world.pull(7, {LOCK: _lock("13.0.0")})
    second, second_merge = world.pull(8, {LOCK: _lock("12.3.1")})
    fake = _GitHub([_node(7, first, first_merge), _node(8, second, second_merge)], refuse={7})
    assert _run(world, fake) == (1, [])
    assert [kind for kind, *_ in fake.writes] == ["comment", "enqueue"]
    out, err = capsys.readouterr()
    assert "#7 (update/7): failed: " in err
    assert "Resource not accessible by integration" in err
    assert f"#8 (update/8): queued at {second[:12]}" in out
