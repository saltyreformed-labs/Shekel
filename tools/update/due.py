"""A release due, told to the developer (ruling plan#2829, which delivers ``balance:R-BAL296``'s
weekly release, ``plan#1029``): while ``dev`` and ``main`` pin different versions, one open issue
mentions him and lists what a release of ``dev`` would move, and it closes once a release
carries them.  He still approves each release and logs out as usual.

**One question.**  What a release of ``dev`` would move is
:func:`tools.quill.door.versions_waiting`, the one ``quill next`` asks too, over every file that
pins versions (ruling plan#2876): ``main`` runs its own copy of every workflow, so an action only
the update's own jobs use waits for a release too.  In a week that moved only such a pin, the
release this asks for changes nothing the app runs; each line names its file, and the issue can
wait open for the next release, mentioning no one again.

**State is read, never stored.**  Each run lists the repository's open issues and finds its
own (:func:`_ours`): the exact title, opened by the workflow's own token.  The repository is
public, so anyone may open an issue so titled, and a stranger's would otherwise stop the
mention.  What it then writes is :func:`writes`:

- versions waiting and none open: it opens one, which mentions him.
- versions waiting and one open: its text follows the list, as a stale list misleads, and only
  when the list moved.  That edit's text names him by no mention, so it can be none, whatever
  GitHub tells of a mention an edit leaves in place (which is not measured).
- nothing waiting and one open: it closes it.
- nothing waiting and none open: nothing.

Two of its own open (one a person reopened) are each followed or closed alike.  A person
closing it while versions wait reads as none open, so the next run opens another.  A hotfix on
``main`` moving a pin reads as a move back until ``dev`` is resynced, and the issue overstates
for that while: no release makes that move, as ``/release`` stops at its step 6 on a merge of
``main`` whose tree is not the cut's.  The next run after the resync takes it off the list.

**It reads the two tips as they are now** (:func:`tools.quill.door.fetch`), never as its
checkout left them: ``actions/checkout`` writes ``origin/main`` as the run's own commit
(``ref-helper.ts``'s ``getRefSpec``, read 2026-11-04 at the pinned commit), which on a re-run of
an old run is older than a release since, so what that release carried would read as waiting;
and it fetches no ``dev``.

**What it runs on.**  Its job (``.github/workflows/updates.yml``'s ``due``) holds only the
workflow's own token, widened to ``issues: write`` for that job alone, as the alert's is, and
the runner's own ``gh`` writes (:func:`tools.update.alert.sent`).  Quill's one door, through
which this reads the walk, imports ``requests`` and ``cryptography``, so the job installs the
``landing`` group, as the queue does; a failed install fails the job, which the alert reports.

Usage, at the root of a checkout whose ``origin`` is this repository, which serves both tips
with no credential, being public::

    GH_TOKEN=<token> GITHUB_REPOSITORY=<owner>/<name> python -m tools.update.due

It prints a line for each write and one saying how many versions wait.  Exit status: 0 the
issue is as the list says; 1 a read or a write failed, each other write still tried, or a
variable is unset, saying which.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from tools.ci.arcs import REPO
from tools.ci.gitcmd import GitError
from tools.quill.door import Moved, NotVersions, described, fetch, versions_waiting
from tools.update.alert import ISSUE_TOKEN_VARIABLE, sent
from tools.update.app import DEVELOPER, REPOSITORY_VARIABLE, unset

#: The title this module's issue goes by, read back each run: its one record.
TITLE = "A release is due: dev and main pin different versions"
#: The login GitHub gives the workflow's own token (measured 2026-11-04: an issue such a token
#: opened, read through GitHub's search).
BOT = "github-actions[bot]"
#: What closes the issue.
CLOSED = {"state": "closed", "state_reason": "completed"}


def _text(versions: Sequence[Moved]) -> str:
    """The issue's text but for its mention, naming each of ``versions``, each in a code span,
    so nothing a version spells reads as a mention or a link."""
    lines = "\n".join(f"- `{moved}`" for moved in versions)
    return (f"A release of `dev` would move these versions, from `main`'s to `dev`'s:\n\n"
            f"{lines}\n\n"
            "This list follows `dev` each day, and this issue closes once a release carries "
            "them (ruling plan#2829).")


def _mentioning(text: str) -> str:
    """``text`` as the issue opens with it: mentioning the developer, which is what reaches
    him (measured 2026-10-27, on the tracker)."""
    return f"@{DEVELOPER} {text}"


def writes(versions: Sequence[Moved],
           ours: Sequence[dict[str, object]]) -> list[tuple[int | None, dict[str, str]]]:
    """What brings ``ours``, each open issue of this module's, to what ``versions`` says (the
    module's four cases): each write's issue number, None for a new issue, and its body."""
    if not versions:
        return [(issue["number"], CLOSED) for issue in ours]
    text = _text(versions)
    if not ours:
        return [(None, {"title": TITLE, "body": _mentioning(text)})]
    return [(issue["number"], {"body": text}) for issue in ours
            if issue["body"] not in (text, _mentioning(text))]


def listed(repository: str) -> subprocess.CompletedProcess:
    """Each open issue of ``repository`` the runner's ``gh`` reads, a JSON object a line, every
    page of them: its number, title, body and who opened it (measured 2026-11-04 with gh
    2.102.1 on this repository, a body's newlines escaped)."""
    return subprocess.run(["gh", "api", f"repos/{repository}/issues?state=open&per_page=100",
                           "--paginate", "--jq", ".[] | {number, title, body, by: .user.login}"],
                          capture_output=True, text=True, check=False)


def _ours(listing: str) -> list[dict[str, object]]:
    """Each issue of ``listing`` (:func:`listed`'s output) that is this module's."""
    return [issue for issue in (json.loads(line) for line in listing.split("\n") if line)
            if issue["title"] == TITLE and issue["by"] == BOT]


def run(repository: str, versions: Sequence[Moved],
        listing: Callable[[str], subprocess.CompletedProcess] = listed,
        send: Callable[..., subprocess.CompletedProcess] = sent) -> int:
    """Bring ``repository``'s issue to what ``versions`` says (:func:`writes`), printing each
    write; exit as the module says."""
    read = listing(repository)
    if read.returncode:
        print(f"update due: no issue was read: {read.stderr.strip()}", file=sys.stderr)
        return 1
    failed = 0
    for number, write in writes(versions, _ours(read.stdout)):
        done = send(repository, write, number)
        if done.returncode:
            print(f"update due: an issue was not written: {done.stderr.strip()}",
                  file=sys.stderr)
            failed = 1
        else:
            print(f"update due: wrote {done.stdout.strip()}")
    print(f"update due: versions waiting for a release: {len(versions)}")
    return failed


def main(argv: Sequence[str] | None = None, root: Path = REPO) -> int:
    """Run over the checkout at ``root`` and the repository the environment names, ``gh``
    reading the token from it."""
    argparse.ArgumentParser(prog="python -m tools.update.due",
                            description="A release due, told to the developer (ruling "
                                        "plan#2829).").parse_args(argv)
    if missing := unset(ISSUE_TOKEN_VARIABLE, REPOSITORY_VARIABLE):
        print(f"update due: {', '.join(missing)} unset, so nothing was read", file=sys.stderr)
        return 1
    try:
        fetch(root)
        versions = versions_waiting(root)
    except (GitError, NotVersions) as error:
        print(f"update due: nothing was read: {described(error)}", file=sys.stderr)
        return 1
    return run(os.environ[REPOSITORY_VARIABLE], versions)


if __name__ == "__main__":
    sys.exit(main())
