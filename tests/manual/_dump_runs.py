"""The half the production-dump runners share: their arguments and report, and each side's run.

``run_deploy_rewrites_on_dump.py`` and ``run_statements_on_dump.py`` each bring the release
kit's bench up on a production dump with a base side and a head side, and differ in what they
measure there.  What they share is here, once:

* **the runner's frame** (:func:`main`): its arguments (``--dump``, ``--out``, ``--base``,
  ``--head``), its report lines written to ``<out>/run.txt`` however the run ends, and its exit
  status, a failure of the run reading 2 and never 1, which says figures part;
* **a side's script run** (:func:`run_pinned_on`): through ``run_pinned.py`` from the side's
  worktree on a copy of the dump, in the environment the kit gives a side, with the run's
  proof that the script imported that side's own ``app`` (``run_pinned``'s
  :data:`~tests.manual.run_pinned.IMPORTED` line), so a pair that reads alike cannot be one
  tree's code measured twice;
* **a pair's comparison** (:func:`parted`), byte for byte.

``run_worked_out_ledger_on_dump.py`` measures one side with no pair, and takes only that side's
environment from here (:func:`side_environment`).
"""

import argparse
import difflib
import os
import pathlib
import sys
from collections import namedtuple

import psycopg

from tests.manual.run_pinned import IMPORTED
from tools.rehearsal import bench, sides

#: The runner that pins a script's clock, from THIS tree (it imports nothing of either side;
#: its ``IMPORTED`` is how it names the ``app`` its script imported).
PINNED = pathlib.Path(__file__).resolve().parent / "run_pinned.py"

# What every side's run in one bench run shares: the instant its clock is
# frozen at (the deploys') and the run's secret key.
Pinned = namedtuple("Pinned", "moment secret")


def side_environment(state, url, secret):
    """Return the environment a measurement from *state*'s tree on *url* runs in.

    The kit's (``sides.side_environment``), but THIS interpreter's directory
    first on ``PATH``: the suite's helpers need ``pytest``, which a side's
    production pins do not hold.

    Args:
        state: The side (:class:`tools.rehearsal.bench.State`).
        url: The copy the measurement reads.
        secret: The run's secret key.
    """
    return {
        **sides.side_environment(state, url, secret),
        "PATH": os.pathsep.join((str(pathlib.Path(sys.executable).parent),
                                 "/usr/bin", "/bin")),
    }


def read_its_own(stderr, state):
    """Whether a :data:`PINNED` run imported ``app`` from *state*'s worktree.

    Args:
        stderr: The run's standard error, which ends with ``run_pinned``'s
            line naming the ``app`` it imported.
        state: The side whose worktree the ``app`` must be under.
    """
    named = [line.removeprefix(IMPORTED) for line in stderr.splitlines()
             if line.startswith(IMPORTED)]
    return len(named) == 1 and pathlib.Path(named[0]).is_relative_to(
        state.worktree)


def run_pinned_on(script, state, url, pinned, written):
    """Run *script* from *state*'s worktree on *url*, writing *written*, the clock frozen.

    Under THIS interpreter, through :data:`PINNED`, in :func:`side_environment`,
    with *written* its one argument; what it printed and its standard error go
    to *written*'s log beside it, ``<written's stem>_log.txt``.

    Args:
        script: The script's path, inside *state*'s worktree.
        state: The side whose ``app`` the script must import.
        url: The copy it reads.
        pinned: The run's :data:`Pinned`.
        written: The path the script writes its output to.

    Returns:
        ``None`` when it exited 0 having imported *state*'s own ``app``, else
        a sentence saying which of the two it did not do.

    Raises:
        bench.BenchError: The interpreter could not be run, or *state*'s tree
            cannot give production's environment.
    """
    log = written.with_name(f"{written.stem}_log.txt")
    with log.open("w", encoding="utf-8") as logged:
        done = bench.run(
            [sys.executable, str(PINNED), pinned.moment.isoformat(),
             str(script), str(written)],
            side_environment(state, url, pinned.secret), stdout=logged,
            cwd=state.worktree,
        )
        logged.write(done.stderr)
    if done.returncode:
        # Its last words, before the line ``run_pinned`` always ends on.
        said = [line for line in done.stderr.strip().splitlines()
                if not line.startswith(IMPORTED)]
        return f"exited {done.returncode}: {said[-1:]}"
    if not read_its_own(done.stderr, state):
        return f"imported an app outside {state.worktree}"
    return None


def parted(first, second, names):
    """Return how the files *first* and *second* part, as unified-diff lines.

    Args:
        first: One side's file.
        second: The other's.
        names: The two sides' names, which head the diff.

    Returns:
        ``[]`` when the two are byte-identical, else the diff's lines (a
        sentence saying so, where they part in nothing a line diff shows).
    """
    if first.read_bytes() == second.read_bytes():
        return []
    texts = [path.read_text(encoding="utf-8") for path in (first, second)]
    return list(difflib.unified_diff(
        texts[0].splitlines(), texts[1].splitlines(), *names, lineterm="",
    )) or [f"{first} and {second} part in their line endings alone"]


def write_lines(path, lines):
    """Write *lines* to *path*, one per line."""
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def main(measure, label, description, argv=None):
    """Parse a dump runner's arguments and run *measure*; the exit status.

    Args:
        measure: The runner's measurement, called as ``measure(dump, out,
            (base, head), say)`` and returning its exit status; each report
            line it hands *say* is printed and written to ``<out>/run.txt``,
            however the run ends.
        label: How the runner names itself on its last line.
        description: The runner's one-line description, for ``--help``.
        argv: The arguments, ``None`` for the command line's.

    Returns:
        *measure*'s status, or 2 when the bench could not be made or torn
        down, a copy could not be read, or a file could not be written.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--dump", type=pathlib.Path, required=True,
                        help="a pg_dump -Fc archive of production")
    parser.add_argument("--out", type=pathlib.Path, required=True,
                        help="a directory to create for the run's files")
    parser.add_argument("--base", required=True,
                        help="the commit the card was claimed from")
    parser.add_argument("--head", default="HEAD",
                        help="the commit to measure (default HEAD)")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True)
    lines = []

    def say(*said):
        """Record *said* for ``run.txt`` and print it."""
        lines.extend(said)
        print("\n".join(said))

    try:
        status = measure(args.dump, args.out, (args.base, args.head), say)
    except (bench.BenchError, OSError, psycopg.Error) as exc:
        # A failure of the run is never exit 1, which says figures part.
        print(f"[{label}] {exc}", file=sys.stderr)
        return 2
    finally:
        write_lines(args.out / "run.txt", lines)
    print(f"[{label}] exit {status}; the run's files are in {args.out}")
    return status
