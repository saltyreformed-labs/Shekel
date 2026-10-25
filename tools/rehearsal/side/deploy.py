"""The side probe's deploy (step X-cx's K1b-2): a side's ``scripts/init_database.py``, entrypoint
step 3, run as the image runs it, under the instant the rehearsal pins every side's clock at
(``design.md`` s.3 item 4; the K1b plan's D2).

Run BY PATH under the side's venv, with the side's worktree as its directory, in the
environment :func:`tools.rehearsal.rows.side_environment` builds::

    <side venv>/bin/python <kit>/tools/rehearsal/side/deploy.py <ISO 8601 instant>

THE CLOCK.  The instant is frozen (``time_machine``, no tick) before the step's first line runs,
so the wall clocks the step and the app read -- ``datetime.now()``, ``date.today()``,
``time.time()`` -- read it.  The orchestrator writes it with its fixed UTC offset, which
``time_machine`` applies without touching the process's zone: that stays the ``TZ`` the
environment carries from the side's production compose file, so local time reads noon.
``time.monotonic()`` is not frozen, so a timeout still runs out.  What no pin here reaches is
PostgreSQL's clock: every row stamp is the server's ``now()``, so a row a deploy writes carries
the moment the deploy ran (``kit_design/design_corrections.md`` entry 1), and a column that
defaults to ``CURRENT_DATE`` (``budget.transaction_entries.purchased_on``,
``budget.escrow_components.effective_date``) takes the cluster's real day in UTC, not the pinned
one, in a row a deploy inserts without setting it.

THE STEP RUNS AS ``python scripts/init_database.py`` HAS IT: as ``__main__``, with ``__file__``
its absolute path (the one name the step reads, through ``abspath``, to find its tree), the
argument list naming only it, and ``sys.path[0]`` the tree's absolute ``scripts/``.  Python made
``sys.path[0]`` this file's directory, which is the KIT's and must lend the step nothing.  The
one difference left is ``sys.argv[0]``, absolute here where a shell gives it as typed; nothing in
the step or in ``app`` reads ``sys.argv``.  A step that raises or exits non-zero ends this
process the same way.
"""
import datetime
import runpy
import sys
from pathlib import Path

import time_machine

#: The deploy step, from the side's worktree.
_STEP = Path("scripts") / "init_database.py"


def main() -> None:
    """Freeze the clock at ``sys.argv[1]`` and run the side's deploy step in it."""
    instant = datetime.datetime.fromisoformat(sys.argv[1])
    step = Path.cwd() / _STEP
    sys.argv = [str(step)]
    sys.path[0] = str(step.parent)
    with time_machine.travel(instant, tick=False):
        runpy.run_path(str(step), run_name="__main__")


if __name__ == "__main__":
    main()
