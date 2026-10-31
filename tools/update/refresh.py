"""The Monday lock refresh (ruling plan#2351, R-BAL294's lock refresh): every package the lock
holds moved as far as ``uv lock --upgrade`` moves it, but each move that needs a card, so the
week's pull request lands by auto-merge with none.

**It runs at the root of a checkout of ``dev``** whose ``.venv`` holds the lock's ``build``
group (``.github/actions/install-lock``'s install set ``build``): ``uv lock`` builds the
metadata of a package the lock holds only as source with those tools, unisolated
(``pyproject.toml``'s ``no-build-isolation-package``), so the run executes package code and
the workflow gives its job no secret.  The 7-day cooldown is ``pyproject.toml``'s
``exclude-newer``, which the lock keeps, so no flag here states it.  uv is the Dockerfile's
(the install set puts it on ``PATH``), so the lock's header, which another uv would write
otherwise, is as the landing check requires.

**What needs a card is the landing check's to say** (:mod:`tools.quill._versions`, through
the door), never this module's: a move across a major, and a move that adds or removes a
package.  Each is held back and the lock made again from ``dev``'s, until no move needs one
(:func:`refreshed`): a package crossing a major is held below the next major
(``--upgrade-package 'P<ceiling>'``, measured 2026-10-31: pyee held at 13.0.1 while six
packages moved), so it still takes the minors and patches of its own major; a package whose
move brings or drops a package is held at its version.  A hold uv does not keep, or a change
no hold can undo (the lock's header), fails the run, which the workflow reports.  The lock
the run settles on is judged whole by the landing check's own walk before it is written.

**Each package that needs a card is prepared as a change of its own** (ruling plan#2450,
:func:`prepared`), to wait for one: each the week holds back, and each direct package a lock
with every range of ``pyproject.toml`` lifted (:mod:`tools.update.pyproject`) moves past its
requirement, across a major past a range or to any newer release past an exact pin
(measured 2026-10-31: pyee 14.0.0, rumdl 0.3.0 and setuptools 84.2.1), its requirement
moved too where uv, moving it with the file as it is, falls short of that release.  Each is
made on top of the week's lock with ``uv lock --upgrade-package P``, not on ``dev``'s: from
``dev``'s, pyee moves nowhere, as ``dev``'s playwright 1.63.0 caps it below 14 and only the
week's 1.64.1 admits 14.0.0 (measured 2026-10-31).  A requirement moved past what uv can
lock alone (another package caps it) still arrives, as the edit alone and uv's refusal, so
CI is red until a session moves what uv names with it; a held package uv moves nowhere on
top of the week has no change of its own, since what holds it is another package's move,
which has one.

Usage, at the checkout's root::

    python -m tools.update.refresh <out>

It writes ``<out>/lock/uv.lock``, ``<out>/lock/title.txt`` and ``<out>/lock/body.md`` when
the lock moves, and nothing when it does not; and for each package prepared alone,
``<out>/major/<package>/`` holding ``pyproject.toml`` where its requirement moves,
``uv.lock`` where uv locks it, ``title.txt`` and ``body.md``.  The checkout's own lock and
``pyproject.toml`` are left as they were.  Exit status: 0 refreshed (or nothing to move); 1
the refresh failed, saying why.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from tools.quill.door import (
    LOCK,
    LockChange,
    Move,
    NotVersions,
    ceiling,
    lock_change,
    lock_moves,
    lock_unversioned,
)
from tools.update.pyproject import (
    PYPROJECT,
    Requirement,
    RequirementError,
    lifted,
    moved,
    requirements,
    rewritten,
)

#: How the lock is made again with each hold: given ``uv lock --upgrade``'s extra arguments,
#: the lock's text it writes.
Locker = Callable[[Sequence[str]], str]
#: How uv locks: given the text of ``pyproject.toml``, the lock's text it starts from and
#: ``uv lock``'s arguments, the lock's text it writes.
Uv = Callable[[str, str, Sequence[str]], str]
#: uv's exit status when it finds no solution (measured 2026-10-31: pyee required past every
#: playwright's cap); it exits 2 when it cannot reach the index (measured: a refused
#: connection), which fails the run.
_NO_SOLUTION = 1


class RefreshError(RuntimeError):
    """The refresh settled on no lock the landing check passes with no card; its message
    says why."""


@dataclass(frozen=True)
class Hold:
    """How the refresh holds one package back: ``spec``, the version specifier it is held
    to (``<14``, ``==8.2``), and ``why``, a clause saying what its move would have done."""

    spec: str
    why: str


@dataclass(frozen=True)
class Refreshed:
    """The week's lock: its ``text``, each package it ``moves``, and each package it holds
    back (``held``, by name)."""

    text: str
    moves: tuple[Move, ...]
    held: Mapping[str, Hold]


def _holds(old: str, new: str) -> dict[str, Hold]:
    """Each package the change from the lock text ``old`` to ``new`` must hold back to land
    with no card, by name: one crossing a major below its next major (at its version where
    none follows, :func:`ceiling`), and one whose move adds or removes a package at its
    version, which overrides the first.

    Raises:
        NotVersions: as :func:`lock_change` raises it, for a change no hold can undo.
    """
    change = lock_change(old, new)
    holds = {move.what: (Hold(f"<{below}", f"{move.new} is a new major")
                         if (below := ceiling(move.old)) is not None else
                         Hold(f"=={move.old}", f"{move.new} is a new major"))
             for move in change.moves if move.major}
    holds.update({name: Hold(f"=={version}", "its next release adds or removes a package")
                  for name, version in change.bringing.items()})
    return holds


def refreshed(old: str, lock: Locker) -> Refreshed:
    """The week's lock, from the lock text ``old`` (``dev``'s): ``lock`` makes it again,
    from ``old`` each time, with every hold so far, until no new hold is needed; a hold made
    at a version is never loosened.

    Raises:
        RefreshError: when the lock settled on still needs a card (a hold uv did not keep,
            an added package no move brought), or a change no hold can undo, saying why.
    """
    held: dict[str, Hold] = {}
    while True:
        new = lock(tuple(argument for name, hold in sorted(held.items())
                         for argument in ("--upgrade-package", f"{name}{hold.spec}")))
        try:
            needed = _holds(old, new)
        except NotVersions as error:
            raise RefreshError(str(error)) from error
        tighter = {name: hold for name, hold in needed.items()
                   if name not in held or (hold.spec.startswith("==")
                                           and not held[name].spec.startswith("=="))}
        if not tighter:
            break
        held.update(tighter)
    if reasons := lock_unversioned(old, new):
        raise RefreshError("; ".join(reasons))
    return Refreshed(new, lock_change(old, new).moves, held)




@dataclass(frozen=True)
class Alone:
    """One package that needs a card, prepared as a change of its own on top of the week's
    lock (ruling plan#2450): ``move``, the move it is made for, the package from its version
    in the week's lock; ``why`` it needs a card, a clause; ``edits``, each requirement of
    ``pyproject.toml`` it moves, as the file spells it before and after; ``pyproject``, the
    file's text where it moves one, else None; ``lock``, the lock uv writes, and ``change``,
    what that lock moves on top of the week's, each None where uv refused; and ``refusal``,
    uv's words where it refused, else nothing."""

    move: Move
    why: str
    edits: Mapping[str, str]
    pyproject: str | None
    lock: str | None
    change: LockChange | None
    refusal: str


@dataclass(frozen=True)
class Monday:
    """What the Monday refresh prepares: the ``week``'s lock, and each package prepared
    ``alone``, by name."""

    week: Refreshed
    alone: tuple[Alone, ...]


def _past(project: str, entries: Sequence[Requirement], week: Refreshed,
          uv: Uv) -> dict[str, Move]:
    """Each direct package a lock made from ``pyproject.toml`` (text ``project``, its
    requirements ``entries``) with every range lifted moves past its requirement, by name:
    across a major from its version in the week's lock, or to any newer release where the
    file pins it exactly.

    Raises:
        RequirementError: as :func:`rewritten` raises it.
        NotVersions: as :func:`lock_moves` raises it.
        subprocess.CalledProcessError: when uv fails.
    """
    capped = [entry for entry in entries if entry.capped]
    top = uv(rewritten(project, {entry.text: lifted(entry) for entry in capped}), week.text,
             ("--upgrade",))
    exact = {entry.name for entry in capped if entry.exact}
    return {move.what: move for move in lock_moves(week.text, top).moves
            if move.what in {entry.name for entry in capped}
            and (move.major or move.what in exact)}


def _edited(project: str, entries: Sequence[Requirement], week: Refreshed, move: Move,
            uv: Uv) -> Alone:
    """``move``, a direct package past its requirements ``entries`` (:func:`_past`), prepared
    alone: each requirement moved to its new release, then ``uv lock --upgrade-package`` on
    top of the week's lock; where uv finds no solution, the requirements alone and uv's
    words.

    Raises:
        RequirementError: as :func:`rewritten` raises it.
        NotVersions: as :func:`lock_moves` raises it.
        subprocess.CalledProcessError: when uv fails other than by finding no solution.
    """
    edits = {entry.text: moved(entry, move.new) for entry in entries}
    text = rewritten(project, edits)
    why = (f"{move.new} is {'a new major, ' if move.major else ''}past {PYPROJECT}'s "
           f"{'exact pin' if any(entry.exact for entry in entries) else 'range'}")
    try:
        lock = uv(text, week.text, ("--upgrade-package", move.what))
    except subprocess.CalledProcessError as error:
        if error.returncode != _NO_SOLUTION:
            raise
        return Alone(move, why, edits, text, None, None, error.stderr)
    change = lock_moves(week.text, lock)
    made = {each.what: each for each in change.moves}
    return Alone(made.get(move.what, move), why, edits, text, lock, change, "")


def _unedited(project: str, week: Refreshed, name: str, uv: Uv) -> Alone | None:
    """The package ``name`` prepared alone with ``pyproject.toml`` (text ``project``) as it
    is: ``uv lock --upgrade-package`` on top of the week's lock, needing a card in the
    landing check's own words; None where uv moves it nowhere, or so that it needs none.

    Raises:
        NotVersions: as :func:`lock_moves` raises it.
        subprocess.CalledProcessError: when uv fails.
    """
    lock = uv(project, week.text, ("--upgrade-package", name))
    change = lock_moves(week.text, lock)
    move = next((each for each in change.moves if each.what == name), None)
    if move is None or not (reasons := lock_unversioned(week.text, lock)):
        return None
    return Alone(move, "; ".join(reasons), {}, None, lock, change, "")


def prepared(project: str, week: Refreshed, uv: Uv) -> tuple[Alone, ...]:
    """Each package that needs a card prepared as a change of its own on top of the week's
    lock (ruling plan#2450), by name: each the week holds back, and each direct package past
    its requirement in ``pyproject.toml`` (text ``project``; :func:`_past`).  Each is moved
    first with the file as it is (:func:`_unedited`); a package past its requirement whose
    move falls short of the version the lifted lock gave it is held below it by that
    requirement, which is moved (:func:`_edited`).  uv is the one judge of what a
    requirement admits: a range that admits a new major (``wheel>=0.37,<1.0`` and wheel
    0.49) moves no requirement.

    Raises:
        RequirementError: when ``pyproject.toml`` holds a requirement the refresh cannot
            lift or move (:func:`requirements`).
        NotVersions: as :func:`lock_moves` raises it.
        subprocess.CalledProcessError: when uv fails other than by finding no solution to
            a moved requirement.
    """
    entries = requirements(project)
    past = _past(project, entries, week, uv)
    found = []
    for name in sorted(past.keys() | week.held.keys()):
        alone = _unedited(project, week, name, uv)
        if name in past and (alone is None or alone.move.new != past[name].new):
            alone = _edited(project, [entry for entry in entries
                                      if entry.name == name and entry.capped],
                            week, past[name], uv)
        if alone is not None:
            found.append(alone)
    return tuple(found)


def _uv(root: Path) -> Uv:
    """A :data:`Uv` running uv in the checkout at ``root``: each time it writes
    ``pyproject.toml`` and the lock it is given, runs ``uv lock`` with the arguments, copies
    what uv says to standard error, and reads the lock uv wrote.

    It raises ``subprocess.CalledProcessError`` when uv fails, uv's words in its ``stderr``.
    """
    def lock(project: str, start: str, arguments: Sequence[str]) -> str:
        """The lock uv writes from ``project`` and ``start`` with ``arguments``."""
        (root / PYPROJECT).write_text(project, encoding="utf-8")
        (root / LOCK).write_text(start, encoding="utf-8")
        done = subprocess.run(["uv", "lock", *arguments], cwd=root, check=False,
                              stderr=subprocess.PIPE, text=True)
        sys.stderr.write(done.stderr)
        done.check_returncode()
        return (root / LOCK).read_text(encoding="utf-8")
    return lock


def _body(week: Refreshed) -> str:
    """The pull request's text for ``week``: what moved, then what was held back."""
    lines = ["The Monday lock refresh (ruling plan#2351): every package `uv lock --upgrade` "
             "moves within its major, each release at least 7 days old. It moves only "
             "versions, so it lands by auto-merge with no card.", "",
             "| Package | From | To |", "| --- | --- | --- |",
             *(f"| {move.what} | {move.old} | {move.new} |" for move in week.moves)]
    if week.held:
        lines += ["", "Held back, each needing a card (ruling plan#2450):", "",
                  *(f"- {name}, held to `{hold.spec}`: {hold.why}."
                    for name, hold in sorted(week.held.items()))]
    return "\n".join(lines) + "\n"


def write(out: Path, week: Refreshed) -> None:
    """``week``'s lock and its pull request's title and text under ``out/lock``, when it
    moves a package; nothing when it moves none."""
    if not week.moves:
        return
    folder = out / "lock"
    folder.mkdir(parents=True)
    (folder / LOCK).write_text(week.text, encoding="utf-8")
    count = len(week.moves)
    (folder / "title.txt").write_text(
        f"chore(deps): the Monday lock refresh moves {count} "
        f"package{'s' if count != 1 else ''}\n", encoding="utf-8")
    (folder / "body.md").write_text(_body(week), encoding="utf-8")


def _alone_body(alone: Alone, week: Refreshed) -> str:
    """The pull request's text for ``alone``, prepared on top of ``week``'s lock: why it
    waits for a card, each requirement it moves, then what its lock moves on top of the
    week's, or uv's refusal."""
    lines = [f"The Monday lock refresh prepares {alone.move.what}'s move as a change of its "
             f"own (ruling plan#2450). It needs a card: {alone.why}. A session reads the "
             "changelog, claims a card and takes this change (ruling plan#2351); until then "
             "the refresh makes it again from `dev` each Monday."]
    if alone.edits:
        lines += ["", f"`{PYPROJECT}` moves its requirement:", "",
                  *(f"- `{old}` to `{new}`" for old, new in alone.edits.items())]
    if alone.change is None:
        lines += ["", "uv cannot lock it alone, so this change holds the requirement and no "
                  "lock, and CI is red until a session moves what uv names with it. uv said:",
                  "", "```text", alone.refusal.strip(), "```"]
        return "\n".join(lines) + "\n"
    if week.moves:
        lines += ["", "It is made on top of this week's lock refresh, so until that pull "
                  "request lands, this one carries its moves too."]
    lines += ["", "On top of the week's lock, the lock moves:", "",
              "| Package | From | To |", "| --- | --- | --- |",
              *(f"| {move.what} | {move.old} | {move.new} |" for move in alone.change.moves)]
    for verb, names in (("adds", alone.change.added), ("removes", alone.change.removed)):
        if names:
            lines += ["", f"It {verb} {', '.join(names)}."]
    return "\n".join(lines) + "\n"


def write_alone(out: Path, alone: Alone, week: Refreshed) -> None:
    """``alone``'s change, prepared on top of ``week``'s lock, and its pull request's title
    and text under ``out/major/<package>``: ``pyproject.toml`` where it moves a
    requirement, and the lock where uv locked it."""
    folder = out / "major" / alone.move.what
    folder.mkdir(parents=True)
    if alone.pyproject is not None:
        (folder / PYPROJECT).write_text(alone.pyproject, encoding="utf-8")
    if alone.lock is not None:
        (folder / LOCK).write_text(alone.lock, encoding="utf-8")
    (folder / "title.txt").write_text(
        f"chore(deps): {alone.move.what} from {alone.move.old} to {alone.move.new}, waiting "
        "for a card\n", encoding="utf-8")
    (folder / "body.md").write_text(_alone_body(alone, week), encoding="utf-8")


def run(root: Path, out: Path, uv: Callable[[Path], Uv] = _uv) -> Monday:
    """Refresh the lock of the checkout at ``root`` (:func:`refreshed`) and prepare each
    package that needs a card alone (:func:`prepared`), each by ``uv``; write each change
    under ``out`` (:func:`write`, :func:`write_alone`); and leave the checkout's lock and
    ``pyproject.toml`` as they were, whatever happens.

    Raises:
        RefreshError: as :func:`refreshed` raises it.
        RequirementError: as :func:`prepared` raises it.
        NotVersions: as :func:`prepared` raises it.
        subprocess.CalledProcessError: when uv fails, but by finding no solution to a moved
            requirement.
    """
    project = (root / PYPROJECT).read_text(encoding="utf-8")
    old = (root / LOCK).read_text(encoding="utf-8")
    locking = uv(root)
    try:
        week = refreshed(old, lambda arguments: locking(project, old, ("--upgrade", *arguments)))
        alone = prepared(project, week, locking)
    finally:
        (root / PYPROJECT).write_text(project, encoding="utf-8")
        (root / LOCK).write_text(old, encoding="utf-8")
    write(out, week)
    for each in alone:
        write_alone(out, each, week)
    return Monday(week, alone)


def main(argv: Sequence[str] | None = None) -> int:
    """Refresh the lock of the checkout at the working directory into the folder named."""
    parser = argparse.ArgumentParser(prog="python -m tools.update.refresh",
                                     description="The Monday lock refresh (ruling plan#2351).")
    parser.add_argument("out", type=Path, help="the folder the week's change is written to")
    args = parser.parse_args(argv)
    try:
        monday = run(Path.cwd(), args.out)
    except RefreshError as error:
        print(f"The refresh settled on no lock that lands with no card: {error}",
              file=sys.stderr)
        return 1
    except (RequirementError, NotVersions) as error:
        print(f"The refresh could not prepare each package that needs a card: {error}",
              file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print(f"uv failed ({error.returncode}): {' '.join(error.cmd)}", file=sys.stderr)
        return 1
    week = monday.week
    print(f"Moved {len(week.moves)} packages; held back {len(week.held)}.")
    for name, hold in sorted(week.held.items()):
        print(f"  held {name} to {hold.spec}: {hold.why}")
    print(f"Prepared {len(monday.alone)} changes alone, each waiting for a card.")
    for alone in monday.alone:
        print(f"  {alone.move.what} {alone.move.old} to {alone.move.new}: {alone.why}"
              + ("; uv cannot lock it alone" if alone.change is None else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
