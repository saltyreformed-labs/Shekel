"""How a door of quill's ends: refused (:class:`Refused`), failed, printed as one line
(:func:`described`), or with what its work returns (:func:`exit_status`).  The ``quill``
command, ``lane`` and the commit hook end through :func:`exit_status`; CI's landing check,
``setup_tracker`` and the release tool print a failure by :func:`described` too.

Split out of ``_command`` and ``_github`` (plan#1990, from plan#1861's review) so the commit
hook, which reads local git alone, imports neither the tracker nor the HTTP path to GitHub:
it imported ``Refused`` and ``exit_status`` from ``_command``, which reads the tracker, and so
loaded ``_github`` and its ``requests`` and ``cryptography``, and a shell whose ``python``
lacked them failed every commit.  This module imports the standard library alone, and the
commit hook's test runs the hook with no installed package (``test_commit_hook``).
"""
from __future__ import annotations

import sys
from collections.abc import Callable


class Refused(Exception):
    """A command that will not run as asked; its message says why."""


def exit_status(act: Callable[[], int], failures: tuple[type[Exception], ...]) -> int:
    """The exit status of a door that runs ``act``, as each door's docstring states it: what
    ``act`` returns; 1 when it is refused (:class:`Refused`), printed ``refused: <why>``; and 2
    when it fails with one of ``failures``, printed ``failed: <the failure>`` on one line
    (:func:`described`); each printed on standard error.  The ONE spelling of those two
    statuses: the ``quill`` command, ``lane`` and the commit hook each run their work through
    it, each naming the failures its work can meet."""
    try:
        return act()
    except Refused as refused:
        print(f"refused: {refused}", file=sys.stderr)
        return 1
    except failures as error:
        print(f"failed: {described(error)}", file=sys.stderr)
        return 2


def described(error: BaseException) -> str:
    """A failure as ONE line: its message, then what was being read when it ended (each
    note :func:`_github.reading` added), each run of whitespace one space.  One line, because
    a scheduled run's failure is pushed with that run's last fifteen log lines
    (``/opt/docker/scripts/notify-failure.sh``), and GitHub's 504 page, which its answer
    text carries, took twenty as quill printed it (its sync at 14:04 2026-10-26, the page
    cut at 500 characters), pushing the failure itself out.  The line is a run's last only
    if what it printed before reached the log first: off a terminal Python buffers stdout,
    where a command's lines go, and not stderr, where this one goes, so the scheduled sync
    runs with ``PYTHONUNBUFFERED=1`` (its unit, ``shekel-plan-sync.service``)."""
    return " ".join("; ".join([str(error), *getattr(error, "__notes__", ())]).split())
