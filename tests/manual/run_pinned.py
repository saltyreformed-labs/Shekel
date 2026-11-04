"""Run a script by path with the clock frozen at one instant: one side's baseline read.

The production-dump runners (``run_deploy_rewrites_on_dump.py``, ``run_statements_on_dump.py``,
each by ``_dump_runs.run_pinned_on``) run a side's figure baselines and render probe
(``verify_balance_baseline.py``, ``verify_statement_baseline.py``,
``verify_render_surfaces.py``) from that side's worktree on that side's deployed copy through
this, so both sides read the one instant every deploy ran under, and a run that crossed
midnight could not read two days.  The clock is frozen as the
release kit's side probes freeze it (``tools/rehearsal/side/deploy.py``: ``time_machine``, no
tick, before the script's first line runs), and the script runs as ``python <script> <args>``
has it: as ``__main__``, ``sys.argv`` naming it and its arguments, ``sys.path[0]`` its own
directory, so the ``app`` and ``tests`` it imports are its own tree's (its path bootstrap puts
that tree first).  Once the script has ended, however it ended, it says on its standard error
which ``app`` it imported (:data:`IMPORTED`), so a caller can show each side read its own.

Usage::

    python tests/manual/run_pinned.py <ISO 8601 instant> <script> [<argument> ...]

Exit status: the script's.
"""

import datetime
import runpy
import sys
from pathlib import Path

import time_machine

#: How the line naming the imported ``app`` opens, as a caller reads it.
IMPORTED = "[run_pinned] app imported from "


def main():
    """Freeze the clock at ``sys.argv[1]`` and run the script ``sys.argv[2]`` in it."""
    instant = datetime.datetime.fromisoformat(sys.argv[1])
    script = Path(sys.argv[2]).resolve()
    sys.argv = [str(script), *sys.argv[3:]]
    sys.path[0] = str(script.parent)
    try:
        with time_machine.travel(instant, tick=False):
            runpy.run_path(str(script), run_name="__main__")
    finally:
        imported = sys.modules.get("app")
        print(IMPORTED + (imported.__file__ if imported else "nowhere"),
              file=sys.stderr)


if __name__ == "__main__":
    main()
