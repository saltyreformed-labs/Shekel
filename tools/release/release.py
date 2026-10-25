"""The ``/release`` skill's tested half: what a release carries, and what to clean up after it.

Not ``quill release``, which deletes a card's claim: this is a release of code to
production (card ``plan#9``).  Everything with a person or a judgment in it -- the
developer's two questions, the CI reads, the rehearsal, the deploy and its checks -- is
the skill's prose (``.claude/skills/release/SKILL.md``, X-cx's piece L9a-2); this
command reads git; the plan's registries as each commit holds them, or, for a cut after
the cutover, the tracker's cards through quill's App; and the dev database server's
listing; and writes nothing.

``plan`` judges the release ``<base>..<cut>`` (:mod:`tools.release._plan`): the steps it
carries, each step's money flag and deploy-together group, the migrations it adds, edits
or deletes; what refuses it; what the developer's approval question must name; and, on a
refusal or two added migrations, the newest earlier cut to offer him.  ``census`` lists
every worktree, every branch ``main`` already holds or a kit names, and every dev
database, marking what this release made: its ``release/<date>`` branch and what each
rehearsal kit's manifest names (:mod:`tools.release._census`).

Usage, from the repository root, after ``git fetch origin``::

    python -m tools.release.release plan --cut SHA [--base origin/main]
        [--unrehearsed KEY|REVISION ...] [--declare KEY=money|none@SOURCE ...]
    python -m tools.release.release census [--main origin/main] [--release-branch release/DATE]
        [--kit DIR ...]

``--unrehearsed`` names a money-moving step (``<arc>:<id>``, or ``plan#N`` after the
cutover) or an added migration's revision for which the skill found no rehearsal kit;
the release is refused for it.  ``--declare`` gives the flag of a carried step no
version of ``steps.md`` holds open (one written already ticked), with the commit or
handoff it was read from; the approval question must name each declaration, and a
declaration for any other step stops the command, as does every declaration on a cut
after the cutover, where each card's flag is its label (R-BAL287).  ``--kit`` names a
rehearsal kit's directory, which holds the kit's ``release-manifest.txt``: one line per
thing the kit made, written before it made it.

Exit status, ``plan``: 0 clean; 1 refused, each reason printed with the cut to offer;
3 the approval question must name what is printed; 2 nothing was judged (the command
line, git, the tracker, an unrehearsed name that names nothing, or a declaration that
fills no unreadable flag).  ``census``: 0 listed, every kit's manifest accepted; 4
listed, but a kit was refused (each reason printed), so the cleanup is NOT proven and
nothing that kit names is marked, whoever else names it; 2 git, the compose file or the
docker listing failed.
A judged run's last line on stdout is its ``verdict:``; exit 2 and a crash print none.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import requests
import yaml

from tools.ci.arcs import REPO
from tools.ci.gitcmd import GitError
from tools.quill.door import CredentialsError, GitHubError, Tracker, TrackerError
from tools.quill.quill import CONNECT
from tools.release._carried import Declaration
from tools.release._census import (
    RELEASE_PREFIX,
    UNPROVEN,
    Census,
    Made,
    census,
    docker_listing,
)
from tools.release._model import ReleaseError
from tools.release._plan import CLEAN, NEEDS_APPROVAL, REFUSED, Inputs, Plan, Verdict, plan

CANNOT = 2
#: A declared flag's two values.
DECLARED = {"money": True, "none": False}


def _release_branch(name: str) -> str:
    """A release branch's name, which must start with :data:`RELEASE_PREFIX`."""
    if not name.startswith(RELEASE_PREFIX) or name == RELEASE_PREFIX:
        raise argparse.ArgumentTypeError(
            f"{name!r} is no release branch: its name starts with {RELEASE_PREFIX!r}")
    return name


def _declaration(text: str) -> Declaration:
    """``KEY=money|none@SOURCE`` read as a :class:`Declaration`; the source is required."""
    key, _, rest = text.partition("=")
    value, _, source = rest.partition("@")
    if not key or value not in DECLARED or not source.strip():
        raise argparse.ArgumentTypeError(
            f"{text!r} is no declaration: write KEY=money@SOURCE or KEY=none@SOURCE, the "
            "source being the commit or handoff the flag was read from")
    return Declaration(key, DECLARED[value], source)


def parser() -> argparse.ArgumentParser:
    """The command line: ``plan`` and ``census``."""
    top = argparse.ArgumentParser(prog="release", description=__doc__.splitlines()[0])
    commands = top.add_subparsers(dest="command", required=True)
    judged = commands.add_parser("plan", help="judge the release <base>..<cut>")
    judged.add_argument("--cut", required=True, help="the commit the release is cut at")
    judged.add_argument("--base", default="origin/main", help="what production runs")
    judged.add_argument("--unrehearsed", action="append", default=[], metavar="KEY|REVISION",
                        help="a money mover or added migration with no rehearsal kit")
    judged.add_argument("--declare", action="append", default=[], type=_declaration,
                        metavar="KEY=money|none@SOURCE",
                        help="the flag of a carried step no version holds open")
    listed = commands.add_parser("census", help="list what may be cleaned up")
    listed.add_argument("--main", default="origin/main", help="the branch released into")
    listed.add_argument("--release-branch", type=_release_branch,
                        help="this release's branch, release/<date>")
    listed.add_argument("--kit", action="append", default=[], metavar="DIR",
                        help="a rehearsal kit's directory, holding its release-manifest.txt; "
                        f"a kit refused exits {UNPROVEN}: the cleanup is not proven")
    return top


def _verdict_lines(verdict: Verdict) -> list[str]:
    """What one cut carries and changes."""
    lines = [f"cut {verdict.cut} {verdict.subject!r}",
             f"steps carried ({len(verdict.carried.steps)}):"]
    for step in verdict.carried.steps:
        money = {True: "yes", False: "no", None: "UNREADABLE"}[step.money] + (
            f" (declared from {step.declared})" if step.declared else "")
        groups = f"  deploy-together: {', '.join(step.groups)}" if step.groups else ""
        undone = "  UNDONE: its marker deleted" if step.undone else ""
        claims = ", ".join(sha[:12] for sha in step.claims)
        lines.append(f"  {step.key}  moves money: {money}{groups}{undone}  "
                     f"({'deleted' if step.undone else 'claimed'} at {claims})")
    for group in verdict.carried.groups:
        lines.append(f"group {group.key}: carried {', '.join(group.carried) or 'none'}; "
                     f"released {', '.join(group.released) or 'none'}; missing "
                     f"{', '.join(group.missing) or 'none'}"
                     + (f"; undone {', '.join(group.undone)}" if group.undone else "")
                     + (f"; named under it {', '.join(group.through)}" if group.through
                        else "")
                     + (f"; the plan will never ship {', '.join(group.dead)}" if group.dead
                        else ""))
    lines.append(f"migrations ({len(verdict.migrations)}):")
    lines += [f"  {m.change:<7} {m.revision or '(no revision id)'}  {m.path}"
              for m in verdict.migrations]
    return lines


def _offer(label: str, offered: Verdict | None) -> list[str]:
    """One offered cut, with its own verdict and what its approval question must name, or
    that no earlier cut qualifies."""
    if offered is None:
        return [f"{label}: none (no earlier cut on dev's first-parent line qualifies)"]
    keys = ", ".join(step.key + (" [moves money]" if step.money else "")
                     + (" [undone]" if step.undone else "")
                     for step in offered.carried.steps) or "no step"
    added = "".join(f"; adds migration {m.revision}" for m in offered.added)
    return [f"{label}: {offered.cut} {offered.subject!r} (carries {keys}{added}; "
            f"exit {offered.status})",
            *(f"  its APPROVAL MUST NAME -- {named}" for named in offered.approvals)]


def plan_lines(judged: Plan) -> list[str]:
    """``plan``'s report, ending with its verdict."""
    verdict = judged.verdict
    lines = [f"release plan: base {judged.base}", *_verdict_lines(verdict)]
    lines += [f"REFUSED -- {reason}" for reason in verdict.refusals]
    lines += [f"APPROVAL MUST NAME -- {named}" for named in verdict.approvals]
    if verdict.refusals:
        lines += _offer("newest earlier cut that passes", judged.passing)
    if len(verdict.added) > 1:
        lines += _offer("newest cut adding at most one migration", judged.one_migration)
    word = {CLEAN: "clean", REFUSED: "refused",
            NEEDS_APPROVAL: "approval must name the above"}[verdict.status]
    lines.append(f"verdict: {word} (exit {verdict.status})")
    return lines


def census_lines(found: Census) -> list[str]:
    """``census``'s report, ending with its verdict: each thing listed, MADE or other,
    with what locates it; then each kit, accepted with what it names that is GONE, or
    REFUSED with each reason and each entry it names, present or absent."""
    lines = [f"census (read-only) after a release into {found.main}"]
    for title, things in (("worktrees", found.worktrees),
                          (f"branches whose tip {found.main[:12]} holds, or a kit names",
                           found.branches),
                          ("dev databases", found.databases)):
        lines.append(f"{title} ({len(things)}):")
        lines += [f"  {'MADE ' if thing.made else 'other'} {thing.name}"
                  + (f"  ({thing.where})" if thing.where else "") for thing in things]
    for kit in found.kits:
        if kit.refusals:
            lines += [f"kit {kit.directory}: REFUSED -- {reason}" for reason in kit.refusals]
            lines += [f"  named {entry.kind} {entry.name} "
                      f"({'present' if entry.present else 'absent'})" for entry in kit.entries]
        else:
            lines.append(f"kit {kit.directory}: accepted, {len(kit.entries)} named")
            lines += [f"  GONE  {entry.kind} {entry.name}"
                      for entry in kit.entries if not entry.present]
    refused = sum(1 for kit in found.kits if kit.refusals)
    lines.append("verdict: proven (exit 0)" if found.proven else
                 f"verdict: cleanup NOT proven, {refused} of {len(found.kits)} kits refused "
                 f"(exit {UNPROVEN})")
    return lines


def main(argv: list[str] | None = None, root: Path = REPO,
         listing: Callable[[list[str]], list[str]] = docker_listing,
         connect: Callable[[], Tracker] | None = None) -> int:
    """Run one command on the repository at ``root``, listing the dev databases with
    ``listing`` and reaching the tracker with ``connect`` (none given: a cut after the
    cutover judges nothing; only the command line, run as ``python -m``, gives quill's
    :data:`~tools.quill.quill.CONNECT`, so no other caller, a test above all, reaches the
    live tracker unless it says so); its exit status."""
    args = parser().parse_args(argv)
    try:
        if args.command == "plan":
            judged = plan(root, args.base, args.cut,
                          Inputs(args.unrehearsed, args.declare, connect))
            print("\n".join(plan_lines(judged)))
            return judged.verdict.status
        found = census(root, args.main, Made(args.release_branch, tuple(args.kit)), listing)
        print("\n".join(census_lines(found)))
        return 0 if found.proven else UNPROVEN
    except (ReleaseError, GitError, OSError, UnicodeDecodeError, yaml.YAMLError,
            subprocess.CalledProcessError, GitHubError, TrackerError, CredentialsError,
            requests.RequestException) as error:
        print(f"release {args.command}: nothing was judged: {error}", file=sys.stderr)
        return CANNOT


if __name__ == "__main__":
    sys.exit(main(connect=CONNECT))
