"""The Monday update's publish (rulings plan#2351, plan#2448, plan#2450): each change the
refresh prepared made one commit on the ``dev`` commit the refresh read, on its branch, its pull
request into ``dev`` opened or refreshed, and each branch whose change is gone deleted.

**It runs main's own code holding the update App's token, and no package code**
(``.github/workflows/updates.yml``'s ``publish`` job, with the ``landing`` group alone).  What
it publishes is the folder the ``refresh`` job wrote, which ran package code and held no
secret (:mod:`tools.update.folder`), read as data and refused whole where it holds anything
else.  The most a change can then do is open a pull request, which the landing check and the
daily queue (:mod:`tools.update.queue`) judge like any other.

**Each change's one commit has the ``dev`` commit the refresh read as its one parent**, never
``dev``'s tip when the publish runs: a lock made from one ``dev`` and committed on a later one
would undo whatever the later one moved in it, where a commit on the one it was made from
merges with the later one as any branch does.  That commit must be ``dev``'s or in its history
(GitHub's compare), or nothing is published.

Each commit is made through GitHub's API: a tree on that commit's tree holding the change's
files, then the commit, which GitHub signs as the App's.  A branch already holding that tree
on that parent is left as it is, so publishing again starts no CI run and drops no pull
request from the merge queue.  A branch of the folder's layout (:func:`folder.ours`) that the
folder no longer names is deleted, which closes its pull request.

Usage, at the root of a checkout of this repository::

    UPDATE_TOKEN=<token> GITHUB_REPOSITORY=<owner>/<name> \\
        python -m tools.update.publish <folder> <dev-commit>

It prints a line for each branch, what it did.  Exit status: 0 published; 1 the folder or the
commit refused, a write GitHub refused (each other branch still tried), or a variable unset,
saying which.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import requests

from tools.ci.gitcmd import FULL_ID
from tools.quill.door import GitHub, GitHubError, described, reading
from tools.update import folder
from tools.update.app import INTO, REPOSITORY_VARIABLE, TOKEN_VARIABLE, unset

#: A regular file's mode in a git tree, each file a change carries.
_FILE = "100644"
#: GitHub's compare status when the commit compared from is the other's or in its history.
_IN_HISTORY = frozenset({"identical", "ahead"})


class Refused(ValueError):
    """What the publish was handed cannot be published; the message says why."""


@dataclass(frozen=True)
class Base:
    """The ``dev`` commit the refresh read, which each change's commit is made on: its
    ``commit`` id and its ``tree``."""

    commit: str
    tree: str


def _base(github: GitHub, repository: str, base: str) -> Base:
    """``base``, the ``dev`` commit the refresh read, with its tree.

    Raises:
        Refused: when ``base`` is no full commit id, or neither ``dev``'s tip nor in its
            history.
        GitHubError, requests.RequestException: as a read raises them.
    """
    if not FULL_ID.fullmatch(base):
        raise Refused(f"{base!r} is no full commit id")
    with reading(f"{base[:12]} against {INTO}"):
        status = github.rest("GET", f"/repos/{repository}/compare/{base}...{INTO}")["status"]
    if status not in _IN_HISTORY:
        raise Refused(f"{base[:12]} is not {INTO}'s, nor in its history ({status})")
    with reading(f"commit {base[:12]}"):
        tree = github.rest("GET", f"/repos/{repository}/git/commits/{base}")["tree"]["sha"]
    return Base(base, tree)


def _branches(github: GitHub, repository: str) -> dict[str, str]:
    """Each branch of the folder's layout (:func:`folder.ours`), by name, with its commit."""
    with reading(f"the branches under {folder.PREFIX}"):
        refs = github.rest("GET", f"/repos/{repository}/git/matching-refs/heads/{folder.PREFIX}")
    named = {ref["ref"].removeprefix("refs/heads/"): ref["object"]["sha"] for ref in refs}
    return {branch: sha for branch, sha in named.items() if folder.ours(branch)}


def _held(github: GitHub, repository: str, sha: str, tree: str, base: Base) -> bool:
    """Whether commit ``sha`` holds ``tree`` on ``base`` alone."""
    with reading(f"commit {sha[:12]}"):
        commit = github.rest("GET", f"/repos/{repository}/git/commits/{sha}")
    return (commit["tree"]["sha"] == tree
            and [parent["sha"] for parent in commit["parents"]] == [base.commit])


def _committed(github: GitHub, repository: str, change: folder.Change, base: Base,
               held: str | None) -> str:
    """``change`` as one commit on ``base`` on its branch, which held ``held`` (None when it
    did not exist): what was done."""
    made = github.rest("POST", f"/repos/{repository}/git/trees", {
        "base_tree": base.tree,
        "tree": [{"path": name, "mode": _FILE, "type": "blob", "content": text}
                 for name, text in change.files.items()]})["sha"]
    if held is not None and _held(github, repository, held, made, base):
        return f"left at {held[:12]}, holding this change on {base.commit[:12]}"
    sha = github.rest("POST", f"/repos/{repository}/git/commits", {
        "message": f"{change.title}\n", "tree": made, "parents": [base.commit]})["sha"]
    if held is None:
        github.rest("POST", f"/repos/{repository}/git/refs",
                    {"ref": f"refs/heads/{change.branch}", "sha": sha})
        return f"made at {sha[:12]}"
    github.rest("PATCH", f"/repos/{repository}/git/refs/heads/{change.branch}",
                {"sha": sha, "force": True})
    return f"moved from {held[:12]} to {sha[:12]}"


def _pulled(github: GitHub, repository: str, change: folder.Change) -> str:
    """``change``'s pull request into :data:`INTO` opened, or its title and text refreshed:
    what was done."""
    owner = repository.split("/", 1)[0]
    with reading(f"the pull requests from {change.branch}"):
        pulls = github.rest("GET", f"/repos/{repository}/pulls?state=open&base={INTO}"
                                   f"&head={owner}:{change.branch}")
    if pulls:
        number = pulls[0]["number"]
        github.rest("PATCH", f"/repos/{repository}/pulls/{number}",
                    {"title": change.title, "body": change.body})
        return f"pull request #{number} refreshed"
    number = github.rest("POST", f"/repos/{repository}/pulls", {
        "title": change.title, "body": change.body, "head": change.branch,
        "base": INTO})["number"]
    return f"pull request #{number} opened"


def run(github: GitHub, repository: str, out: Path, base: str) -> int:
    """Publish each change in the folder ``out``, prepared from ``dev``'s commit ``base``,
    and delete each branch whose change is gone, printing a line for each; exit as the
    module says."""
    try:
        changes = folder.read(out)
        made_on = _base(github, repository, base)
        branches = _branches(github, repository)
    except (folder.FolderError, Refused, GitHubError, requests.RequestException) as error:
        print(f"update publish: nothing was published: {described(error)}", file=sys.stderr)
        return 1
    failed = 0
    for change in changes:
        try:
            done = _committed(github, repository, change, made_on,
                              branches.get(change.branch))
            print(f"{change.branch}: {done}; {_pulled(github, repository, change)}")
        except (GitHubError, requests.RequestException) as error:
            print(f"{change.branch}: failed: {described(error)}", file=sys.stderr)
            failed += 1
    for branch in sorted(branches.keys() - {change.branch for change in changes}):
        try:
            github.rest("DELETE", f"/repos/{repository}/git/refs/heads/{branch}")
            print(f"{branch}: deleted, its change gone, which closes its pull request")
        except (GitHubError, requests.RequestException) as error:
            print(f"{branch}: failed: {described(error)}", file=sys.stderr)
            failed += 1
    print(f"update publish: {len(changes)} changes from {base[:12]}; {failed} failed")
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    """Publish the folder the command line names, prepared from the ``dev`` commit it names,
    with the token and to the repository the environment names."""
    parser = argparse.ArgumentParser(prog="python -m tools.update.publish",
                                     description="The Monday update's publish (ruling "
                                                 "plan#2351).")
    parser.add_argument("out", type=Path, help="the folder the refresh wrote")
    parser.add_argument("base", help="the dev commit the refresh read")
    args = parser.parse_args(argv)
    if missing := unset(TOKEN_VARIABLE, REPOSITORY_VARIABLE):
        print(f"update publish: {', '.join(missing)} unset, so nothing was published",
              file=sys.stderr)
        return 1
    return run(GitHub(os.environ[TOKEN_VARIABLE]), os.environ[REPOSITORY_VARIABLE], args.out,
               args.base)


if __name__ == "__main__":
    sys.exit(main())
