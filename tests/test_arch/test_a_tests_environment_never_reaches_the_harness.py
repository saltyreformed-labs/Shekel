"""Architecture test: no database setting a test makes reaches the test harness.

Ruling plan#3900.  libpq reads its connection settings from the environment
(``PGHOSTADDR``, ``PGSERVICE`` and dozens more) each time a connection opens, and
a test sets them with ``monkeypatch`` to test code that reads them
(``tests/test_scripts/test_dev_two_factor.py``).  pytest-flask's autouse fixtures
take the shared ``monkeypatch`` before ``db``, so a test's settings are still in
force when ``db``'s teardown drops its database.  Every clone, drop and catalog
read of the harness goes over the worker's one admin connection, opened before
any test (``tests/_worker_database.py``), so none of them reads those settings.
"""

from tests import _worker_database as worker_database

#: A world-free database under this worker's own name, which the start-up sweep
#: would collect as its own leftover were it ever left standing.
_PROBE = f"{worker_database._WORKER_DB_NAME}_settings_probe"


def test_a_clone_a_lookup_and_a_drop_read_no_setting_the_test_made(monkeypatch):
    """The harness clones, finds and drops a database under a service no file defines.

    ``PGSERVICE`` naming a service that no service file holds makes every
    connection opened under it fail at once, with no network involved
    (``definition of service ... not found``), so a helper that opened a
    connection of its own would raise here.  This test's own ``db`` teardown
    runs under the setting too, as every test's runs under its own.
    """
    monkeypatch.setenv("PGSERVICE", "no_service_file_defines_this_one")
    worker_database._clone_worker_database(_PROBE)
    try:
        assert worker_database.database_exists(_PROBE), (
            f"{_PROBE} was cloned and the catalog does not hold it"
        )
    finally:
        worker_database._drop_worker_database(_PROBE)
    assert not worker_database.database_exists(_PROBE), (
        f"{_PROBE} was dropped and the catalog still holds it"
    )
