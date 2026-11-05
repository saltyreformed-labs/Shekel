"""The test runner gives every run a private cluster and leaves nothing behind.

Steps ``balance:X-br-2`` (the cluster), ``X-br-3`` (the daemon and the socket)
and ``X-br-4`` (it stopped being optional).  The suite clones a per-worker
database from ``shekel_test_template`` for every test, and that template used
to live on ONE container shared by every worktree -- which is why the repo
carried a suite-slot lock, a hygiene-restart flag, a live-backend probe,
``TEST_DB_PREFIX`` and ``TEST_TEMPLATE_DATABASE``.  ``X-br-4`` deleted all
five and the shared path with them, so there is no flag here to grade any
more: a container per run is what the wrapper does.

**None of the per-run block was graded when it was written**, and a review
said so.  What that review found is what these tests pin, and one of its two
findings has since been deleted rather than fixed:

* A signal to the wrapper could not be serviced while pytest ran in the
  FOREGROUND, so ``timeout`` stopped working and the SIGKILL escalation left
  a live container plus an orphaned pytest.  pytest is backgrounded and
  ``wait``ed now, because ``wait`` is interruptible, and since K1a commit B2
  so is the cluster's start, each with SIGINT at its default
  (``test_signals.py``).
* ``TEST_DB_PER_RUN`` was INHERITED by pytest, so a child that re-invoked the
  wrapper started two more containers nested inside the suite.  The wrapper
  scrubbed the flag before handing the environment on.  With the flag gone
  that scrub is gone too -- there is no shared path left for a child to be
  pushed onto, because there is no shared path at all.

The cluster's own life -- its socket directory, ``docker run``, readiness and
teardown -- is ``scripts/private_cluster.py``'s since step X-cx's K1a, which
moved it out of the wrapper; its own cases (the socket path's byte budget
among them) are ``test_private_cluster.py``.  The cases here drive it THROUGH
the wrapper, which makes its token, arms the traps and hands pytest the DSNs.

These drive a stub ``docker`` and a stub ``pytest`` on ``PATH``, so they need
no daemon, no image and no database, and they run in CI.  Each runs a copy of
the wrapper in a tree of its own (``_helpers._wrapper_copy``), because the wrapper
syncs the venv of the tree it finds itself in; that sync is ``test_venv.py``'s,
which also runs the real uv, over a project with no dependency.  The suite-scale
behaviour they stand in for was measured separately, on 2026-09-05 at
``balance:X-br-2``: 13,156 passed in 342.8 s in a private cluster against
356.4 s on the shared one.
"""
