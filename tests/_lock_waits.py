"""Hold a row on one connection, and run an act that waits on it until the holder commits.

A test of what an act does to a row another transaction is writing holds the
row on a connection of its own, runs the act in a worker thread, and commits
the holder once the act is waiting on it (:func:`act_past_a_held_row`):
committing before the act reaches the row would grade nothing.  Moved here
from ``tests/test_routes/test_strangers_sign_in.py`` when a second module
needed it (plan#3120).
"""

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from flask import Flask
from sqlalchemy import Connection, text

from app.extensions import db

_Result = TypeVar("_Result")

#: How long a thread is given to reach a lock and wait on it, and to finish
#: once the holder lets go.  Generous: the arm polls and moves on the moment
#: the wait is visible, so the figure only bounds a failure.
THREAD_DEADLINE_S = 20.0


def _wait_until_a_statement_waits_on_a_lock(pending):
    """Block until a statement in THIS database waits on a lock.

    Read from ``pg_stat_activity`` on a connection of its own, scoped to this
    test's database: every xdist worker's clone is a database of its own.

    Args:
        pending: The worker's future; one that finishes without waiting is
            reported with its own outcome at once.

    Raises:
        AssertionError: When the worker finished without waiting, or no
            wait appears within the deadline -- either way the arm would
            grade nothing.
    """
    deadline = time.monotonic() + THREAD_DEADLINE_S
    with db.engine.connect() as watcher:
        while time.monotonic() < deadline:
            if pending.done():
                raise AssertionError(
                    f"the request finished without waiting: {pending.result()!r}"
                )
            waiting = watcher.execute(text(
                "SELECT count(*) FROM pg_stat_activity"
                " WHERE datname = current_database()"
                " AND wait_event_type = 'Lock' AND pid <> pg_backend_pid()"
            )).scalar()
            watcher.rollback()
            if waiting:
                return
            time.sleep(0.02)
    raise AssertionError("the request never waited on a lock")


def act_past_a_held_row(
    app: Flask, holder: Connection, act: Callable[[], _Result],
) -> _Result:
    """Run *act* while *holder* holds a row it waits on; commit *holder* once it waits.

    *act* runs in a worker thread, in an app context of its own, so it has a
    session and a connection of its own, as a production request has.

    Args:
        app: The application.
        holder: A connection whose open transaction holds the row; committed
            here once *act* waits, and rolled back on every exit, which is a
            no-op after the commit.
        act: What to run, with no arguments.

    Returns:
        What *act* returned.
    """
    def run() -> _Result:
        """*act*, in an app context of its own."""
        with app.app_context():
            return act()

    with ThreadPoolExecutor(max_workers=1) as worker:
        pending = worker.submit(run)
        try:
            _wait_until_a_statement_waits_on_a_lock(pending)
            holder.commit()
        finally:
            # Released BEFORE the pool's exit joins the thread, which would
            # otherwise wait on the row for ever.  A no-op after the commit.
            holder.rollback()
        return pending.result(timeout=THREAD_DEADLINE_S)
