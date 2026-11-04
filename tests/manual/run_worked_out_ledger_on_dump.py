"""Run ``verify_worked_out_ledger.py`` on a production dump, restored by the release kit.

Plan task plan#2528's procedure (plan step plan#2513, "The proof", item 2), built from the
release rehearsal kit's bench (``tools/rehearsal``) rather than beside it:

1. the dump is restored once into a private cluster no other session can move, and cloned for
   the run (``bench.Bench``), beside a detached worktree of this repository at the commit to
   measure, and that commit's production pins in a venv of their own;
2. the commit's deploy step (``scripts/init_database.py``) runs on the clone as production's
   entrypoint runs it, under those pins, in production's environment, with the clock pinned at
   noon of the dump's civil day (``rows.deploy``): the step that brings the store to target;
3. the harness runs from that worktree on the clone, pinned at the same instant, under THIS
   interpreter, since it needs the suite's helpers and a side's production pins do not hold
   ``pytest``.

The bench tears everything down on the way out and refuses a teardown that left anything.

Usage, with production's locale pinned as ``scripts/test.sh`` pins it (the image builder
creates the app)::

    LC_ALL=C.UTF-8 .venv/bin/python tests/manual/run_worked_out_ledger_on_dump.py \\
        --dump <pg_dump -Fc file> --out <a directory that does not exist yet> \\
        [--revision <commit, default HEAD>]

It writes three files into ``--out``: ``deploy.txt`` (the commit, the pinned instant and the
deploy step's verdict), ``report.txt`` (the harness's report) and ``harness_log.txt`` (what the
app logged while it ran).  Keep ``--out`` in the handoff folder: production figures never enter
the repository (ruling R-BAL132), and a commit or pull request cites the path.

Exit status: the harness's (0, 1 or 2, as its docstring says); 2 also when the bench could not
be made or its teardown left something, the deploy step refused the copy, or this run or the
harness crashed or was killed (``exit_status``).
"""

import argparse
import os
import pathlib
import secrets
import sys

# The repository root, so ``tools`` is importable when this runs by path.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

# Pylint: ``wrong-import-position`` -- these must follow the path bootstrap
# above; there is no import order that resolves ``tools`` before it runs.
# pylint: disable=wrong-import-position
from tests.manual import exit_status

exit_status.arm(__name__)

from tools.rehearsal import bench, rows

#: The harness, as a path inside the measured worktree.
_HARNESS = pathlib.Path("tests") / "manual" / "verify_worked_out_ledger.py"
#: The one state the bench makes: the commit measured.
_STATE = "measured"


def _measure(state, environment, moment, out):
    """Run the harness from *state*'s worktree on its copy; what its exit status says.

    Args:
        state: The side measured, whose worktree holds the harness.
        environment: The harness's environment.
        moment: The instant its clock is pinned at.
        out: The directory its report and log are written to.

    Returns:
        The harness's verdict, or 2 for any other status (``exit_status``).
    """
    with (out / "harness_log.txt").open("w", encoding="utf-8") as log:
        measured = bench.run(
            [sys.executable, str(state.worktree / _HARNESS),
             "--report", str(out / "report.txt"), "--at", moment.isoformat()],
            environment, stdout=log, cwd=state.worktree,
        )
        log.write(measured.stderr)
    return exit_status.measurement_status(measured.returncode)


def run(dump, out, revision):
    """Bring the bench up on *dump*, deploy, measure into *out*; the exit status.

    Args:
        dump: The ``pg_dump -Fc`` archive.
        out: The directory the three files are written to (it exists, empty).
        revision: The commit of this repository to deploy and measure.

    Returns:
        The harness's exit status, or 2 when the deploy step refused.

    Raises:
        bench.BenchError: The bench could not be made, or its teardown left
            something.
    """
    with bench.Bench(dump, {_STATE: revision}) as made:
        state = made.made[_STATE]
        moment = made.listing.noon(rows.image_environment(state.worktree)["TZ"])
        secret = secrets.token_hex(32)
        refused = rows.deploy(state, state.url, secret, moment)
        (out / "deploy.txt").write_text(
            f"commit {state.commit}, deployed under the clock pinned at "
            f"{moment.isoformat()}: "
            f"{'exit 0' if refused is None else 'REFUSED, ' + refused}\n",
            encoding="utf-8",
        )
        if refused is not None:
            return 2
        environment = {
            **rows.side_environment(state, state.url, secret),
            "PATH": os.pathsep.join((str(pathlib.Path(sys.executable).parent),
                                     "/usr/bin", "/bin")),
        }
        return _measure(state, environment, moment, out)


def main(argv=None):
    """Parse the arguments and run; the exit status."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dump", type=pathlib.Path, required=True,
                        help="a pg_dump -Fc archive of production")
    parser.add_argument("--out", type=pathlib.Path, required=True,
                        help="a directory to create for the run's three files")
    parser.add_argument("--revision", default="HEAD",
                        help="the commit to deploy and measure (default HEAD)")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True)
    try:
        status = run(args.dump, args.out, args.revision)
    except bench.BenchError as exc:
        print(f"[worked-out ledger] {exc}", file=sys.stderr)
        return 2
    print(f"[worked-out ledger] exit {status}; the report is "
          f"{args.out / 'report.txt'}")
    return status


if __name__ == "__main__":
    sys.exit(main())
