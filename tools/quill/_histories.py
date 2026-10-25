"""The git histories CI's landing check is tested over, the tracker it reads, and the one way
its tests judge one.

Shared by ``test_landing.py`` (rules 1-7), ``test_landing_done.py`` (rule 8) and
``test_landing_tasks.py`` (a task's done file), so each history and each card's node is
spelled once.  Every history is a throwaway repository's (:mod:`tools.ci.scratch`), its
markers written as files and its merges REAL ones (:func:`merge`: git's three-way merge, two
parents), judged as CI judges one: the merge of the pull request's head into its base's tip
(:func:`judging`).  The tracker is a stand-in for GitHub answering the REAL card-only reader
(:class:`Lookup`, :func:`reader`), each card a node in the shape GitHub answers a read without
the board (:func:`card_node`), held key for key to the recorded lookup's by ``test_landing.py``.
Nothing here calls GitHub.
"""
from __future__ import annotations

import copy
import re

from tools.ci.scratch import commit, commit_files, point_dev, point_main, repository, run
from tools.quill._git import marker
from tools.quill._tracker import CardReader
from tools.quill.landing import PullRequest, main
from tools.quill.setup_tracker import PLAN

#: When the tests' claims are made, and when a later one is.
MON, TUE = "2026-10-19T13:00:00Z", "2026-10-20T09:30:00Z"

#: A person's login, whose close is a decision of theirs.
PERSON = "SaltyReformed"


def card_node(number, kind="step", parent=None, labels=("balance",)):
    """Open card ``number``'s node as GitHub answers the card-only reader (no board items),
    never closed or reopened, its sub-issues left for :func:`tracker` to fill from the
    cards naming it their parent."""
    return {
        "id": f"I_{number}", "number": number, "fullDatabaseId": str(1000 + number),
        "title": f"card {number}", "state": "OPEN", "stateReason": None,
        "issueType": {"name": kind} if kind else None,
        "labels": {"totalCount": len(labels), "nodes": [{"name": name} for name in labels]},
        "parent": ({"number": parent, "repository": {"nameWithOwner": PLAN.full_name}}
                   if parent else None),
        "subIssues": {"totalCount": 0, "nodes": []},
        "blockedBy": {"totalCount": 0, "nodes": []},
        "timelineItems": {"nodes": []},
    }


def closed(node, reason, closer):
    """``node`` closed as ``reason`` (``COMPLETED``, ``NOT_PLANNED``) by the login ``closer``."""
    return {**node, "state": "CLOSED", "stateReason": reason,
            "timelineItems": {"nodes": [{"__typename": "ClosedEvent",
                                         "actor": {"login": closer}}]}}


def tracker(*cards):
    """The tracker holding ``cards``, by number, each card's sub-issues the cards naming it
    their parent, as GitHub lists them."""
    nodes = {node["number"]: copy.deepcopy(node) for node in cards}
    for node in nodes.values():
        if node["parent"]:
            listed = nodes[node["parent"]["number"]]["subIssues"]
            listed["nodes"].append({"number": node["number"], "state": node["state"],
                                    "issueType": node["issueType"],
                                    "repository": node["parent"]["repository"]})
            listed["totalCount"] += 1
    return nodes


class Lookup:
    """A GitHub answering each lookup of cards by number from ``nodes``, a number it lacks
    answered None (as GitHub answers a number nobody filed); it keeps the numbers each
    lookup asked for.  ``fails``: what every lookup raises instead, if anything."""

    def __init__(self, nodes, fails=None):
        """Hold the tracker's nodes."""
        self.nodes = nodes
        self.fails = fails
        self.asked = []

    def graphql_lookup(self, query):
        """Answer the cards ``query`` asks for by number."""
        if self.fails is not None:
            raise self.fails
        numbers = [int(number) for number in re.findall(r"c(\d+): issue\(number: \1\)", query)]
        self.asked.append(sorted(numbers))
        return {"repository": {f"c{number}": copy.deepcopy(self.nodes.get(number))
                               for number in numbers}}


def reader(github):
    """A connection making the card-only reader of the plan's tracker through ``github``."""
    return lambda: CardReader(github, PLAN)


def never():
    """A connection no test may make: the tracker is read only when a card must be."""
    raise AssertionError("the tracker was read")


def start(parent):
    """A repository under ``parent`` whose ``origin/dev`` and ``origin/main`` hold one base
    commit (its ``app.py``), the start of every history; the repository and the base."""
    root = repository(parent)
    base = commit_files(root, commit(root, "root"), "base", {"app.py": "base\n"})
    point_dev(root, base)
    point_main(root, base)
    return root, base


def merge(root, first, second, message="merge"):
    """``second`` merged into ``first`` as git's three-way merge writes it: a commit of the
    two, ``first`` its first parent; its sha."""
    tree = run(root, "merge-tree", "--write-tree", first, second)
    return run(root, "commit-tree", tree, "-p", first, "-p", second, "-m", message)


def claim(root, on, number, when=MON):
    """Card ``number``'s branch as ``quill claim`` starts it on ``on`` and its session builds
    it: the claim commit adding the marker ``claimed <when>``, then a commit of its work; the
    claim commit and the branch's tip."""
    claimed = commit_files(root, on, f"claim plan#{number}", {marker(number): f"claimed {when}\n"})
    return claimed, commit_files(root, claimed, f"work {number}", {f"w{number}.txt": "work\n"})


def landed(root, onto, number, when=MON):
    """Card ``number`` claimed on ``onto`` and its branch merged into it, as its queue lands
    it; the merge."""
    return merge(root, onto, claim(root, onto, number, when)[1], f"Merge card/{number}")


def undone(root, on, number):
    """A revert on ``on`` of card ``number``'s landing: its marker and work deleted."""
    return commit_files(root, on, f"Revert plan#{number}",
                        {marker(number): None, f"w{number}.txt": None})


def pull(head, sha, base="dev"):
    """A read of the pull request of branch ``head`` (None: another repository's) at ``sha``
    into ``base``, as :func:`landing.pull_request` makes one."""
    return lambda number: PullRequest(number, base, head, sha)


def judging(capsys, root):
    """The check run over the merge-queue group of pull request #600 in ``root``, as a test
    calls it: with ``onto``, the tip of ``base``, branch ``head`` at ``head_sha``, and
    ``connect``, which makes the tracker's reader, it judges GitHub's merge of the two and
    answers its exit status, output and error, as pytest's ``capsys`` captured them."""
    def _judge(onto, head_sha, head, base, connect):
        """Judge pull request #600 of ``head`` into ``base`` (:func:`judging`)."""
        (point_dev if base == "dev" else point_main)(root, onto)
        merged = merge(root, onto, head_sha, f"Merge pull request #600 from {head}")
        status = main(["merge_group", merged,
                       f"refs/heads/gh-readonly-queue/{base}/pr-600-{onto}"],
                      connect, pull(head, head_sha, base), root)
        out = capsys.readouterr()
        return status, out.out, out.err

    return _judge


def verdict(head, changes, refused, base="dev", tasks=0):
    """The check's last line for pull request #600 of branch ``head`` into ``base``, which
    writes or deletes ``changes`` ship markers and ``tasks`` task done files."""
    files = f", {tasks} task done file change(s)" if tasks else ""
    return (f"landing check: pull request #600 into {base} from {head}: {changes} ship marker "
            f"change(s){files}, {refused} refused")
