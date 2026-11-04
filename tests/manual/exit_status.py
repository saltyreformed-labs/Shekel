"""A measurement on a production copy that measured nothing exits 2, never Python's own 1.

Five production-dump measurements read one exit status, each arming this module: the three
runners ``run_*_on_dump.py``, and ``verify_worked_out_ledger.py`` and
``measure_worked_out_read.py``, which they run.  It is 0 clean, 1 figures part (a difference it
measured, a question for the developer), 2 nothing measured.  The figure baselines and the render
probe a runner also runs do not read it: any non-zero of theirs is nothing measured to the runner
(``_dump_runs.run_pinned_on``).  Python ends any uncaught exception with status 1, so however
carefully each of the five chose its own statuses, each read every crash as figures that moved: on
a dump older than head's migrations
``measure_worked_out_read.py`` raised in ``ref_cache.init`` before it read a row, and exited as
if a count had moved (measured 2026-11-04, ``PRE_FICA_DEDUCTION``, finding plan#2826).

:func:`arm` takes that one status out of the interpreter's hands, before the script's other
imports, so it holds for an import that fails, an app that will not start, a report that cannot
be opened and a crash part way, whatever it raises: a list of what a run can raise is one
unlisted kind from reading as figures that moved.  Nothing is swallowed, the traceback printing
as it always does, and a ``sys.exit`` reaches no exception hook, so a status a script returns is
its own.

:func:`measurement_status` is the other end: what a runner may take from a measurement it ran,
since a process killed by a signal has a negative status, which a runner taking the highest of
its statuses would read as clean.
"""

import os
import sys

#: The status every measurement here documents for "nothing was measured".
NOTHING_MEASURED = 2
#: The statuses only a measurement that reached its verdict returns.
_VERDICTS = (0, 1)


def _print_and_end(kind, value, trace):
    """Print the traceback as Python would, then end with :data:`NOTHING_MEASURED`.

    ``os._exit``, because the interpreter exits 1 once an exception hook returns.  It flushes
    nothing and runs no ``atexit`` handler, so the streams are flushed here; each ``with`` the
    script held was released as the exception unwound to get here.
    """
    try:
        sys.__excepthook__(kind, value, trace)
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        os._exit(NOTHING_MEASURED)


def arm(name):
    """Make an uncaught exception exit :data:`NOTHING_MEASURED` in a script run as one.

    Args:
        name: The calling module's ``__name__``: only ``"__main__"`` arms, so a test or a
            sibling script importing the module leaves its process's hook alone.
    """
    if name == "__main__":
        sys.excepthook = _print_and_end


def measurement_status(returncode):
    """Return what a measurement's *returncode* says: 0 or 1 as it is, anything else 2.

    Args:
        returncode: The process's return code, negative for a signal's death.
    """
    return returncode if returncode in _VERDICTS else NOTHING_MEASURED
