"""The Monday update's publish (rulings plan#2351, plan#2448, plan#2450): each change the
refresh prepared made one commit on the ``dev`` commit the refresh read, on its branch, its pull
request into ``dev`` opened or refreshed, and each branch whose change is gone deleted.

**It runs main's own code holding the update App's token, and no package code but the
``landing`` group's** (``.github/workflows/updates.yml``'s ``publish`` job): ``requests`` and
``cryptography`` and the packages they need, installed from main's lock, each file checked
against its hash.  What it publishes is the folder the ``refresh`` job wrote, which ran
package code and held no secret (:mod:`tools.update.folder`), read as data and refused whole
where it holds anything else; and no workflow restores a cache that code could write (ruling
plan#2471).  The most a change can then do is open a pull request, which the landing check
and the daily queue (:mod:`tools.update.queue`) judge like any other.

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

**No release the developer closed comes back** (rulings plan#2774, plan#2776: a close means
"not this release", for a new major and for any other move that needs a card).  For a
package prepared alone with no pull request open from its branch, the pull requests closed
from it are read: where the developer closed one unmerged while its head proposed the
release the change proposes, nothing is committed or opened (:func:`_declined`).  Any other
release, a close by anyone else (the publish closes one itself by deleting its branch) or a
merged one publishes the change as before, and reopening one is the developer's way back,
as an open one is refreshed.  The release a change proposes is read by one reading
(:func:`_release`) from the files it carries and from those a closed head changed.

Usage, at the root of a checkout of this repository::

    UPDATE_TOKEN=<token> GITHUB_REPOSITORY=<owner>/<name> \\
        python -m tools.update.publish <folder> <dev-commit>

It prints a line for each branch, what it did.  Exit status: 0 published; 1 the folder or the
commit refused, a read or a write GitHub refused or a change's own lock or ``pyproject.toml``
that cannot be read (each other branch still tried), or a variable unset, saying which.
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import requests

from tools.ci.gitcmd import FULL_ID
from tools.quill.door import (
    LOCK,
    GitHub,
    GitHubError,
    NotVersions,
    described,
    lock_pins,
    paged,
    reading,
)
from tools.update import folder
from tools.update.app import DEVELOPER, INTO, REPOSITORY_VARIABLE, TOKEN_VARIABLE, unset
from tools.update.pyproject import PYPROJECT, RequirementError, release, requirements

#: A regular file's mode in a git tree, each file a change carries.
_FILE = "100644"
#: GitHub's compare status when the commit compared from is the other's or in its history.
_IN_HISTORY = frozenset({"identical", "ahead"})
#: The files a change's release is read from (:func:`_release`).
_PROPOSING = (LOCK, PYPROJECT)
#: A page of the pull requests closed unmerged into ``dev`` from one branch, newest first,
#: each with its head, whether it came from a fork, and who closed it last
#: (``timelineItems(last: 1)``: the newest close, with no page of a pull request's events to
#: walk).  It is read under the token's Pull requests permission, as the queue reads its
#: pull requests' timelines (:mod:`tools.update.queue`); whether a GraphQL read needs it is
#: not documented (GitHub's permissions for Apps list REST alone, read 2026-11-03).
_CLOSED = """query($owner: String!, $name: String!, $branch: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(headRefName: $branch, baseRefName: "%s", states: CLOSED, first: 100,
                 after: $after, orderBy: {field: CREATED_AT, direction: DESC}) {
      nodes {
        number isCrossRepository headRefOid
        timelineItems(last: 1, itemTypes: [CLOSED_EVENT]) {
          nodes { ... on ClosedEvent { actor { login } } }
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}""" % INTO


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


def _open(github: GitHub, repository: str, change: folder.Change) -> int | None:
    """The number of the pull request into :data:`INTO` open from ``change``'s branch; None
    where none is."""
    owner = repository.split("/", 1)[0]
    with reading(f"the open pull requests from {change.branch}"):
        pulls = github.rest("GET", f"/repos/{repository}/pulls?state=open&base={INTO}"
                                   f"&head={owner}:{change.branch}")
    return pulls[0]["number"] if pulls else None


def _release(package: str, files: Mapping[str, str]) -> str | None:
    """The release of ``package`` a change carrying ``files`` (each by name, with its text)
    proposes: its version in the lock where the change carries one (:func:`lock_pins`),
    else the release each of its capped requirements in ``pyproject.toml`` was moved to
    (:func:`release`), each of several joined; None where it proposes none.

    Raises:
        NotVersions: as :func:`lock_pins` raises it.
        RequirementError: as :func:`requirements` raises it.
    """
    if LOCK in files:
        found = {pin.version for pin in lock_pins(files[LOCK]) if pin.what == package}
    elif PYPROJECT in files:
        found = {moved for entry in requirements(files[PYPROJECT])
                 if entry.name == package and entry.capped
                 and (moved := release(entry)) is not None}
    else:
        found = set()
    return ", ".join(sorted(found)) or None


def _proposed_at(github: GitHub, repository: str, package: str, sha: str) -> str | None:
    """The release of ``package`` commit ``sha`` proposes (:func:`_release`), read from each
    file it reads that the commit changes from its parent: a head the publish made is one
    commit on the ``dev`` commit it read, so these are the files its change carried.  None
    where the commit proposes none, as a head someone else pushed onto the branch may not,
    or holds a file that cannot be read as one (no base64, no UTF-8, no TOML), so the change
    is published again rather than any later release stopped on it.

    Raises:
        GitHubError, requests.RequestException: as a read raises them.
    """
    with reading(f"the files commit {sha[:12]} changes"):
        changed = github.rest("GET", f"/repos/{repository}/commits/{sha}")["files"]
    held = {}
    for entry in changed:
        if entry["filename"] in _PROPOSING:
            with reading(f"{entry['filename']} at {sha[:12]}"):
                held[entry["filename"]] = github.rest(
                    "GET", f"/repos/{repository}/git/blobs/{entry['sha']}")["content"]
    try:
        return _release(package, {name: base64.b64decode(content).decode("utf-8")
                                  for name, content in held.items()})
    except ValueError:
        return None


def _declined(github: GitHub, repository: str, change: folder.Change) -> str | None:
    """Where ``change`` prepares a package alone: the newest pull request into :data:`INTO`
    from its branch, of this repository, that the developer closed unmerged while its head
    proposed the release the change proposes (:func:`_release`, :func:`_proposed_at`), with
    that release, as a line names them; None where none did, and for the week's lock.  Every
    release he closed is held so, not only the newest: 14.0.0 closed, then 14.0.1 closed,
    then 14.0.1 yanked, 14.0.0 stays closed.  The developer is his account, whoever acts
    as it: a session's close under his ``gh`` counts.  The change's own release is read
    only once he has closed one, so its files are read as nothing more than text until
    then.

    Raises:
        GitHubError, requests.RequestException: as a read raises them.
        NotVersions, RequirementError: as :func:`_release` raises them for the change's own
            files.
    """
    if change.package is None:
        return None
    owner, name = repository.split("/", 1)
    closed, _ = paged(partial(github.graphql, _CLOSED, owner=owner, name=name,
                              branch=change.branch),
                      lambda answer: answer["repository"]["pullRequests"],
                      f"the pull requests closed from {change.branch}")
    theirs = [pull for pull in closed if not pull["isCrossRepository"]
              and (closes := pull["timelineItems"]["nodes"])
              and (closes[0]["actor"] or {}).get("login") == DEVELOPER]
    if not theirs or (proposed := _release(change.package, change.files)) is None:
        return None
    for pull in theirs:
        if _proposed_at(github, repository, change.package, pull["headRefOid"]) == proposed:
            return (f"pull request #{pull['number']}, closed by {DEVELOPER} at "
                    f"{change.package} {proposed}")
    return None


def _pulled(github: GitHub, repository: str, change: folder.Change, number: int | None) -> str:
    """``change``'s pull request into :data:`INTO` opened, or the one open from its branch,
    ``number``, its title and text refreshed: what was done."""
    if number is not None:
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
            number = _open(github, repository, change)
            if number is None and (declined := _declined(github, repository, change)):
                print(f"{change.branch}: left, as its {declined}; its next release is "
                      "published")
                continue
            done = _committed(github, repository, change, made_on,
                              branches.get(change.branch))
            print(f"{change.branch}: {done}; {_pulled(github, repository, change, number)}")
        except (GitHubError, requests.RequestException, NotVersions,
                RequirementError) as error:
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
