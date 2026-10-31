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

Usage, at the checkout's root::

    python -m tools.update.refresh <out>

It writes ``<out>/lock/uv.lock``, ``<out>/lock/title.txt`` and ``<out>/lock/body.md`` when
the lock moves, and nothing when it does not; the checkout's own lock is left as it was.
Exit status: 0 refreshed (or nothing to move); 1 the refresh failed, saying why.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from tools.quill.door import LOCK, Move, NotVersions, ceiling, lock_change, lock_unversioned

#: How the lock is made again with each hold: given ``uv lock --upgrade``'s extra arguments,
#: the lock's text it writes.
Locker = Callable[[Sequence[str]], str]


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


def _uv(root: Path, old: str) -> Locker:
    """A :data:`Locker` running uv in the checkout at ``root``: each time it writes ``old``
    back to the lock, runs ``uv lock --upgrade`` with the extra arguments, and reads what
    uv wrote.

    The locker raises ``subprocess.CalledProcessError`` when uv fails.
    """
    def lock(arguments: Sequence[str]) -> str:
        """The lock uv writes from ``old`` with ``arguments``."""
        (root / LOCK).write_text(old, encoding="utf-8")
        subprocess.run(["uv", "lock", "--upgrade", *arguments], cwd=root, check=True)
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


def run(root: Path, out: Path, locker: Callable[[Path, str], Locker] = _uv) -> Refreshed:
    """Refresh the lock of the checkout at ``root`` (:func:`refreshed`) by ``locker``, write
    the week's change under ``out`` (:func:`write`), and leave the checkout's lock as it
    was, whatever happens.

    Raises:
        RefreshError: as :func:`refreshed` raises it.
        subprocess.CalledProcessError: when uv fails.
    """
    old = (root / LOCK).read_text(encoding="utf-8")
    try:
        week = refreshed(old, locker(root, old))
    finally:
        (root / LOCK).write_text(old, encoding="utf-8")
    write(out, week)
    return week


def main(argv: Sequence[str] | None = None) -> int:
    """Refresh the lock of the checkout at the working directory into the folder named."""
    parser = argparse.ArgumentParser(prog="python -m tools.update.refresh",
                                     description="The Monday lock refresh (ruling plan#2351).")
    parser.add_argument("out", type=Path, help="the folder the week's change is written to")
    args = parser.parse_args(argv)
    try:
        week = run(Path.cwd(), args.out)
    except RefreshError as error:
        print(f"The refresh settled on no lock that lands with no card: {error}",
              file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print(f"uv failed ({error.returncode}): {' '.join(error.cmd)}", file=sys.stderr)
        return 1
    print(f"Moved {len(week.moves)} packages; held back {len(week.held)}.")
    for name, hold in sorted(week.held.items()):
        print(f"  held {name} to {hold.spec}: {hold.why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
