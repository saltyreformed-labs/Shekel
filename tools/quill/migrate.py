"""The ``migrate`` command: X-cx's migration of the plan into the tracker (piece L7).

The plan in ``docs/plans/`` moves into the tracker as cards (ruling ``balance:R-BAL170``;
the L7 design's draft 4).  Every phase READS the registries and arc documents as this
checkout's HEAD commit holds them, from git, whatever the checkout's own files say
(rulings ``balance:R-BAL257``, ``R-BAL259``), so every card quotes the text of the commit
its As filed block names; and the input file the developer approved.

``check`` WRITES NOTHING, anywhere.  It prints what would move, arc by arc,
and what is filed already (:data:`_migrate_source.MAPPED`); the census of every
steps-section line (:func:`_migrate_bodies.census`); how many bare ``#N`` the cards hold,
which GitHub would link to the tracker's card N, by the treatment the input file gives each
(R-BAL277, :mod:`_migrate_numbers`); the writes a run would make, as the plan counts them
(:func:`_migrate_plan.plan`, the one producer of what is left to write): with
``--target``, on that tracker as read, and without it, if every card were new, a card
filed already's links not counted; and every refusal, from the registries
(:mod:`_migrate_source`), the input file (:mod:`_migrate_input`), the cards' text
(:mod:`_migrate_bodies`), their numbers (:mod:`_migrate_numbers`), GitHub's renderer and
the plan -- which runs, like every phase's, only once none of the others refuses, and
without ``--target`` plans every item but the cards filed already, so steps waiting in a
cycle through one of them are refused by ``check --target`` and the runs alone.

**GitHub's renderer grades the cards' numbers** (ruling ``balance:R-BAL355``,
:func:`_migrate_numbers.grade_rendered`) in the context of the tracker ``--target`` names, through
quill's App, once nothing the registries, the input file and the cards' text make is
refused, and before anything is planned or written: by ``check --target``, ``history`` and
``open``.  ``check`` without ``--target`` reaches no tracker, so it prints that the numbers
are not graded; ``verify`` grades the tracker as filed and renders nothing.

``history`` and ``open`` WRITE, on the tracker ``--target`` names (``plan`` or
``rehearsal``, with no default, draft 4 s.6), as quill's App: ``history`` files every
ruling, and ``open``, refused until it has, every other card, then orders the board
(:mod:`_migrate_run`).  Each runs only when nothing above refuses -- the registries, the
input file, the cards' text, its census, its numbers, GitHub's renderer -- and then only on
a tracker the plan refuses nothing on: a plan that refuses anything writes nothing.  A run
cut short is resumed by running the same command again.

``verify`` WRITES NOTHING: it reads the tracker ``--target`` names and grades it against
the registries both ways (:mod:`_migrate_verify`), printing the tracker's own cards, which
no item claims.

Usage, from the repository root::

    python -m tools.quill.migrate check --input PATH [--target plan|rehearsal]
    python -m tools.quill.migrate history --input PATH --target plan|rehearsal
    python -m tools.quill.migrate open --input PATH --target plan|rehearsal
    python -m tools.quill.migrate verify --input PATH --target plan|rehearsal

The input file lives OUTSIDE the repository, and one inside it is refused: its text may
quote a real figure, which the tracker allows and the public repository does not
(``R-BAL172``, ``R-BAL132``; :mod:`_migrate_input`).

Exit status: 0 nothing refused (and for ``history`` and ``open``, every write made; for
``verify``, the tracker holds what the registries make); 1 something refused (each on a
``REFUSED:`` line), and nothing written -- or an error none
of these names (an App key file that cannot be parsed, say), with Python's traceback in
place of any ``REFUSED:`` line, which may follow writes; 2 nothing checked:
the input file could not be read (or is not UTF-8), git could not read HEAD or a planning
file in it (or the file is not text), the input file is inside the repository, the
command line is not the form above, or for ``check --target`` and ``verify`` the tracker
could not be read (the App's key file unreadable, GitHub refusing or not answering, its
renderer included, the connect finding no board for the target or one not linked to its
repository, or the read refused as a run's is, below); 3 a
``history`` or ``open`` run STOPPED, its cause
on a ``stopped:`` line: the App's key file could not be read; GitHub refused or did not
answer (the connect and its renderer included); the connect found the target no board, or
one not linked to its repository (:func:`_tracker.board_of`); a write's answer read back
otherwise than it was sent (a create's type, labels or milestone, or a ruling's close: "fix it by
hand"); the tracker's read was refused (its numbering probe, two milestones of one title,
or a read GitHub cut short: a card holding more labels, sub-issues, blockers or board items
than one read returns); a card the run writes on was under another parent, or gone; or,
after the writes, the tracker read again had writes left or refused anything, or the board
did not show a card, a move or its desired order, or could not be ordered (it held a card
the desired order does not).  What it wrote stands; settle what the line names, then run
it again.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import requests

from tools.ci.arcs import ARCS, REPO
from tools.ci.gitcmd import GitError, git
from tools.ci.gitcmd import run as run_git
from tools.quill._github import GitHubError
from tools.quill._migrate_bodies import (
    ON_A_CARD,
    Built,
    Inputs,
    RowText,
    build,
    census,
    row_texts,
)
from tools.quill._migrate_input import TREATMENTS, Decisions, InputError, grade, load
from tools.quill._migrate_numbers import UNDECIDED, grade_rendered, link
from tools.quill._migrate_plan import MILESTONE, Plan, Tracked, plan
from tools.quill._migrate_run import Clock, Filing, Run, Stopped, read_tracked
from tools.quill._migrate_source import (
    MAPPED,
    Registries,
    Source,
    read_registries,
    read_source,
)
from tools.quill._migrate_verify import Unreadable, verify
from tools.quill._tracker import Tracker, TrackerError
from tools.quill.setup_tracker import PLAN, REHEARSAL, Place

#: The kinds of card, in the order a report names them.
KINDS = ("step", "finding", "question", "ruling")
#: The trackers a run may write, and ``check`` and ``verify`` read, by ``--target`` (draft
#: 4 s.6; no default for a run or ``verify``).
TARGETS = {"plan": PLAN, "rehearsal": REHEARSAL}
#: A tracker holding nothing: what ``check`` without ``--target`` counts the writes on, for
#: every item but the cards filed already, whose cards are not new.
_EMPTY = Tracked({}, {}, (), "")


@dataclass(frozen=True)
class Prepared:
    """What every phase reads before any tracker: the registries' items, the input file's
    decisions, each card's text as filed (its numbers written) and as built (before they
    are, which GitHub's renderer grades it against:
    :func:`_migrate_numbers.grade_rendered`), the
    census, how many bare ``#N`` each treatment took, and every refusal of the five."""

    source: Source
    decisions: Decisions
    built: Mapping[str, Built]
    as_built: Mapping[str, Built]
    places: Mapping[str, Counter]
    treated: Counter
    refusals: tuple[str, ...]


def prepare(registries: Registries, data: dict) -> Prepared:
    """The registries read (:func:`_migrate_source.read_source`), the input file graded
    (:func:`_migrate_input.grade`), the cards' text built and its census taken
    (:mod:`_migrate_bodies`), then its bare ``#N`` written as the input file decides
    (:func:`_migrate_numbers.link`), with the refusals of all five: what ``check`` reports
    and a writing phase gates on.  The census reads the text as built, before its numbers
    are written; every phase files and grades it after."""
    source = read_source(registries)
    decisions, problems = grade(data, source, registries)
    inputs = Inputs(source, registries, decisions)
    built, built_problems = build(inputs)
    places, census_problems = census(inputs, built)
    linked = link(built, decisions.numbers)
    return Prepared(source, decisions, linked.built, built, places, linked.counts,
                    (*source.refusals, *problems, *built_problems, *census_problems,
                     *linked.refusals))


def _filing(prepared: Prepared) -> Filing:
    """What a run files, and ``check`` and ``verify`` plan by: every item, its card text and
    each outcome's milestone."""
    return Filing(prepared.source.items, prepared.built, prepared.decisions.milestones)


def check(registries: Registries, prepared: Prepared, tracked: Tracked | None = None,
          rendered: Sequence[str] | None = None) -> tuple[list[str], int]:
    """The report ``check`` prints, and how many refusals it holds: what GitHub's renderer
    refused of the numbers, ``rendered`` (None: not graded, with no ``--target`` or while
    ``prepared`` refuses anything), and its writes counted on ``tracked``, the ``--target``
    tracker as read, or if every card were new (None) -- none while anything is refused."""
    source = prepared.source
    refused = [*prepared.refusals, *(rendered or ())]
    found = None if refused else _planned(_filing(prepared), tracked)
    refusals = [*refused, *(found.refusals if found else ())]
    lines = [*_counts("check", source, registries), *_census_lines(prepared.places),
             _linked(prepared.treated)]
    if rendered is None:
        lines.append("bare #N: not graded by GitHub's renderer (`check --target` grades them "
                     "once nothing above is refused)")
    lines.append(_writes(found, tracked is None))
    lines += [f"REPORT: {line}" for line in source.reports]
    lines += [f"REFUSED: {line}" for line in refusals]
    lines.append(f"{len(refusals)} refusal(s)")
    return lines, len(refusals)


def _planned(filing: Filing, tracked: Tracked | None) -> Plan:
    """The plan ``check`` counts the writes by: on ``tracked``, for every item; on a tracker
    holding nothing (None), for every item but the cards filed already, whose cards are not
    new, so their own links are not counted, nor a cycle through one of them seen."""
    if tracked is not None:
        return plan(filing.items, filing.built, filing.milestones, tracked)
    return plan(tuple(item for item in filing.items if item.key not in MAPPED), filing.built,
                filing.milestones, _EMPTY)


def _counts(command: str, source: Source, registries: Registries) -> list[str]:
    """The cards that move, by arc and kind: for ``check`` those still to file, for
    ``verify`` those the migration files."""
    kinds = Counter((item.arc, item.kind) for item in source.items)
    filed = ", ".join(f"{key} is plan#{number}" for key, number in sorted(MAPPED.items()))
    return [f"{command} at {registries.commit}: {len(source.items)} cards, "
            f"{len(source.items) - len(MAPPED)} "
            + ("to file" if command == "check" else "the migration files")
            + (f" ({filed} already)" if filed else ""),
            *(f"  {arc}: " + ", ".join(f"{kind}s {kinds[arc, kind]}" for kind in KINDS)
              for arc in ARCS)]


def _census_lines(places: Mapping[str, Counter]) -> list[str]:
    """The census, one line an arc: its non-blank lines, those on a card first."""
    lines = []
    for arc, counted in places.items():
        rest = sorted((place, count) for place, count in counted.items() if place != ON_A_CARD)
        lines.append(f"census {arc}: {sum(counted.values())} lines -- {ON_A_CARD} "
                     f"{counted[ON_A_CARD]}" + "".join(f"; {place} {count}"
                                                       for place, count in rest))
    return lines


def _linked(treated: Counter) -> str:
    """How many bare ``#N`` the cards hold, by the treatment each took
    (:func:`_migrate_numbers.link`), a refused card's counted undecided."""
    return ("bare #N, which GitHub links to the tracker's card N: "
            + (", ".join(f"{treatment} {treated[treatment]}"
                         for treatment in (*TREATMENTS, UNDECIDED) if treated[treatment])
               or "none"))


def _writes(found: Plan | None, new: bool) -> str:
    """The writes the plan ``found`` holds (:func:`_planned`), every card ``new`` or on the
    tracker as read; none counted while anything is refused."""
    if found is None or found.refusals:
        return "writes: not counted while anything is refused"
    milestones = sum(write.what == MILESTONE for write in found.freeze)
    return ("writes if every card is new, not counting the links of a card filed already "
            "(`check --target` counts them)" if new else "writes left on the tracker") + (
                f": {len(found.history)} filing the rulings, then {len(found.freeze)} filing "
                f"the rest and the {milestones} milestones, before the board's moves")


def parser() -> argparse.ArgumentParser:
    """The command line: ``check --input PATH``, ``--target`` optional; ``history``,
    ``open`` and ``verify`` with ``--target`` besides."""
    top = argparse.ArgumentParser(prog="migrate", description=__doc__.splitlines()[0])
    commands = top.add_subparsers(dest="command", required=True)
    for command, said in (("check", "what would move, and what is refused"),
                          ("history", "file every ruling (first)"),
                          ("open", "file every other card, then order the board"),
                          ("verify", "whether the tracker holds what the registries make")):
        each = commands.add_parser(command, help=said)
        each.add_argument("--input", required=True, type=Path,
                          help="the input file, outside the repository")
        each.add_argument("--target", required=command != "check", choices=sorted(TARGETS),
                          help="the tracker written or read: the real one or the rehearsal's")
    return top


def graded(prepared: Prepared, tracker: Tracker) -> tuple[str, ...]:
    """What GitHub's renderer refuses of the cards' numbers on ``tracker``
    (:func:`_migrate_numbers.grade_rendered`), asked once nothing ``prepared`` holds
    refuses."""
    return grade_rendered(prepared.as_built, prepared.built, prepared.decisions.numbers,
                          tracker.render, tracker.place.full_name)


def run(command: str, prepared: Prepared, connect: Callable[[], Tracker],
        clock: Clock) -> int:
    """Run the writing phase ``command`` (``history`` or ``open``) on the tracker
    ``connect`` reaches, once nothing ``prepared`` holds refuses and GitHub's renderer
    refuses none of its numbers; its exit status."""
    if prepared.refusals:
        return _refused(prepared.refusals)
    try:
        writer = Run(connect, _filing(prepared), clock)
        if refusals := graded(prepared, writer.tracker):
            return _refused(refusals)
        refusals = writer.history() if command == "history" else writer.open()
    except (Stopped, GitHubError, TrackerError, requests.RequestException,
            OSError) as error:
        print(f"stopped: {error}", file=sys.stderr)
        return 3
    if refusals:
        return _refused(refusals)
    print(f"{command}: done, {writer.made} write(s) made")
    return 0


def verifying(registries: Registries, prepared: Prepared, tracked: Tracked | None,
              resolves: Callable[[str], bool],
              rows_at: Callable[[str], Mapping[str, frozenset[RowText]]]
              ) -> tuple[list[str], int]:
    """The report ``verify`` prints, and how many refusals it holds: the tracker as read,
    ``tracked`` (None: not read, while ``prepared`` refuses anything), graded
    (:func:`_migrate_verify.verify`), an As filed commit held to ``resolves``, and what
    each item's row writes at it, ``rows_at``'s, to what its row writes here."""
    found = (verify(_filing(prepared), tracked, resolves, rows_at) if tracked is not None
             else None)
    refusals = [*prepared.refusals, *(found.refusals if found else ())]
    lines = [*_counts("verify", prepared.source, registries), *_census_lines(prepared.places)]
    if found is not None and found.own is not None:
        lines.append("the tracker's own cards, no item's: " + (
            ", ".join(f"plan#{number}" for number in found.own) or "none"))
    lines += [f"REFUSED: {line}" for line in refusals]
    lines.append(f"{len(refusals)} refusal(s)" + ("" if found else "; the tracker not read"))
    return lines, len(refusals)


def resolver(root: Path, head: str) -> Callable[[str], bool]:
    """Whether an As filed commit resolves in the repository at ``root``: the full id of a
    commit ``head`` descends from -- git names the commit it reads that text as by that
    very text (``rev-parse --verify <text>^{commit}`` prints it back, which a ref name, a
    short id and a tag's id do not), and it is an ancestor of ``head`` -- each asked of git
    once.  Only a full id reaches ``merge-base``."""
    known: dict[str, bool] = {}

    def resolves(commit: str) -> bool:
        """Whether ``commit`` resolves (:func:`resolver`)."""
        if commit not in known:
            known[commit] = run_git(root, "rev-parse", "--verify", "--quiet",
                                    f"{commit}^{{commit}}").stdout.strip() == commit \
                and run_git(root, "merge-base", "--is-ancestor", commit, head).returncode == 0
        return known[commit]
    return resolves


def row_reader(root: Path, read: Callable[[Path, str], Registries]
               ) -> Callable[[str], Mapping[str, frozenset[RowText]]]:
    """What each item's row writes on its card, by key, as a commit's registries hold the
    rows, read at ``root`` by ``read`` (``verify``'s ``rows_at``):
    :func:`_migrate_bodies.row_texts` over the items :func:`_migrate_source.read_source`
    reads there -- the producer and the rows :func:`prepare` composes every card from --
    and no input file, whose work no As filed block claims.  What that commit's source
    refuses is not asked: a card claims its item's row, and is held to what that row
    writes.  ``verify`` asks each commit once.

    Raises:
        _migrate_verify.Unreadable: From the function made, for a commit whose registries git
            cannot read (a planning file it lacks, or git failing), that are not text, or
            that the plan gate's readers refuse (their AssertionError: a registry's table
            missing, or a row of another width, as at an ancestor older than the tables'
            present shape).
    """
    def rows_at(commit: str) -> Mapping[str, frozenset[RowText]]:
        """What each item's row writes at ``commit`` (:func:`row_reader`)."""
        try:
            return row_texts(read_source(read(root, commit)).items)
        except (OSError, UnicodeDecodeError, GitError, AssertionError) as error:
            raise Unreadable(str(error)) from error
    return rows_at


def _read(command: str, prepared: Prepared,
          tracker: Tracker) -> tuple[Tracked | None, tuple[str, ...] | None]:
    """For ``check`` (``--target``) and ``verify``: the tracker as read (None once GitHub's
    renderer refused anything, as nothing is planned then), and what the renderer refused
    of the numbers (None for ``verify``, which renders nothing)."""
    rendered = graded(prepared, tracker) if command == "check" else None
    return (None if rendered else read_tracked(tracker)), rendered


def _refused(refusals: Sequence[str]) -> int:
    """Print ``refusals``, each on a ``REFUSED:`` line, and that nothing was written; exit
    status 1."""
    print("\n".join([*(f"REFUSED: {line}" for line in refusals),
                     f"{len(refusals)} refusal(s); nothing written"]))
    return 1


def main(argv: Sequence[str] | None = None,
         read: Callable[[Path, str], Registries] = read_registries, root: Path = REPO,
         connect: Callable[[Place], Tracker] = Tracker.connect, clock: Clock = Clock()) -> int:
    """Run one command on the repository at ``root``, reading its registries at HEAD with
    ``read`` (:func:`_migrate_source.read_registries`), and writing (``history``, ``open``)
    or reading (``check --target``, ``verify``) the tracker ``connect`` reaches for the
    ``--target`` place, timed by ``clock``; its exit status."""
    args = parser().parse_args(argv)
    try:
        if args.input.resolve().is_relative_to(root.resolve()):
            raise InputError(f"{args.input} is inside the repository; the input file may "
                             "quote a real figure, so it lives outside it (R-BAL132)")
        data = load(args.input)
        commit = git(root, "rev-parse", "HEAD").strip()
        registries = read(root, commit)
    except (InputError, OSError, UnicodeDecodeError, GitError) as error:
        print(f"failed: {error}", file=sys.stderr)
        return 2
    prepared = prepare(registries, data)
    if args.command in ("history", "open"):
        return run(args.command, prepared, lambda: connect(TARGETS[args.target]), clock)
    tracked, rendered = None, None
    if args.target is not None and not prepared.refusals:
        try:
            tracked, rendered = _read(args.command, prepared, connect(TARGETS[args.target]))
        except (GitHubError, TrackerError, requests.RequestException, OSError) as error:
            print(f"failed: {error}", file=sys.stderr)
            return 2
    lines, refused = (
        check(registries, prepared, tracked, rendered) if args.command == "check" else
        verifying(registries, prepared, tracked, resolver(root, commit), row_reader(root, read)))
    print("\n".join(lines))
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
