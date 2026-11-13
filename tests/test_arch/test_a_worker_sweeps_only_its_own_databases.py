"""Architecture test: a worker's start-up sweep drops only the databases named for it.

Plan task **plan#3639**, ruling **plan#3660**.  ``_bootstrap_worker_database``
(``tests/_worker_database.py``) runs as each worker starts and drops leftovers it can
name as its own: its database and that database's world snapshots.  Every run has a
cluster of its own, so any other worker's database or snapshot on it is a LIVE one; and
pytest-xdist starts a replacement for a crashed worker mid-session, under a new id, so
the sweep runs while its siblings are between tests with no connection open.  A sweep
that reached past its own names dropped their snapshots, and every later test declaring
that world failed at its clone.

The test runs the bootstrap again in this process under a worker id no real worker uses,
on a cluster laid out with one leftover of that id's own and one sibling whose id begins
with the probe's (as ``gw11`` begins with ``gw1``), holding a snapshot of its own and no
connection.  It grades both directions: the leftover goes, the sibling and its snapshot
stay.
"""

import os

import pytest

from tests import _worker_database as worker_database

#: The worker id the bootstrap is run under: no pytest-xdist id (``gw<N>``) and not the
#: single-process one (``main``), so its names are no real worker's.
_PROBE_WORKER = "sweepprobe"

#: The probe's own database, which the bootstrap creates.
_PROBE_DATABASE = f"{worker_database._TEST_DATABASE_PREFIX}_{_PROBE_WORKER}"

#: A world snapshot the probe's id left behind: the sweep's to drop.
_OWN_LEFTOVER = f"{_PROBE_DATABASE}{worker_database._SNAPSHOT_INFIX}probe"

#: A sibling worker whose id the probe's is a prefix of, and that sibling's snapshot.
#: An unescaped ``_`` in the sweep's pattern would match the sibling, and a pattern over
#: every snapshot would match its snapshot.
_SIBLING_DATABASE = f"{_PROBE_DATABASE}1"
_SIBLING_SNAPSHOT = f"{_SIBLING_DATABASE}{worker_database._SNAPSHOT_INFIX}probe"


@pytest.fixture()
def laid_out_cluster():
    """Create the leftover and the sibling's two databases; drop every probe name after."""
    for name in (_OWN_LEFTOVER, _SIBLING_DATABASE, _SIBLING_SNAPSHOT):
        worker_database._clone_worker_database(name)
    yield
    for name in (_OWN_LEFTOVER, _SIBLING_DATABASE, _SIBLING_SNAPSHOT, _PROBE_DATABASE):
        worker_database._drop_worker_database(name)


@pytest.mark.usefixtures("laid_out_cluster")
def test_the_sweep_drops_its_own_leftover_and_no_one_elses(monkeypatch):
    """The probe's own snapshot is dropped; the sibling's database and snapshot are not.

    ``TEST_DATABASE_URL`` is set to its own value first so that the bootstrap's write of
    it is undone with the worker id when the test ends.
    """
    monkeypatch.setenv("TEST_DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("PYTEST_XDIST_WORKER", _PROBE_WORKER)

    assert worker_database._bootstrap_worker_database() == (
        _PROBE_DATABASE, worker_database._WORKER_ADMIN_URL,
    )

    assert not worker_database.database_exists(_OWN_LEFTOVER), (
        f"the sweep left {_OWN_LEFTOVER}, a snapshot named for its own worker, so a "
        "restarted run on a shared cluster would keep a world dated from a dead session"
    )
    dropped = [name for name in (_SIBLING_DATABASE, _SIBLING_SNAPSHOT)
               if not worker_database.database_exists(name)]
    assert not dropped, (
        f"worker {_PROBE_WORKER}'s start-up sweep dropped {dropped}, which belong to "
        f"another worker that was between tests: the sweep reaches past its own names"
    )
