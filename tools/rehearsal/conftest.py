"""The rehearsal kit's test configuration: the ``cluster`` marker.

A test that starts a real private cluster carries it.  CI's every-scope step deselects
those (``-m "not cluster"``), since a registry-only change set cannot change the kit
(the coordinator's K1 leaf-plan ruling, item 3); CI's test job runs them on one shard (ci.yml
step 7c, ``balance:R-BAL327``), and by hand they run as ``pytest tools/rehearsal -c /dev/null
-m cluster``, on the rootless daemon ``scripts/private_cluster.py`` chooses.  The tests run
with ``-c /dev/null``, so no ini file registers the marker: this hook does.

The ``dump`` fixture is the planted archive those tests restore.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from tools.ci import arcs
from tools.rehearsal import bench


def pytest_configure(config: pytest.Config) -> None:
    """Register ``cluster``, so pytest does not warn of it as an unknown marker."""
    config.addinivalue_line(
        "markers", "cluster: starts a real private cluster (scripts/private_cluster.py)")


@pytest.fixture(name="dump", scope="module")
def _dump(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """A ``pg_dump -Fc`` archive of a planted database, begun as production's volume begins
    (``scripts/init_db.sql``: the app's schemas and an empty ``alembic_version``, which the
    deploy step's first build needs) and holding two tables: two rows of money, and one row
    whose stamp the server's clock wrote before any run reads its line (a dump's stamp, which
    the rows compare exactly)."""
    path = tmp_path_factory.mktemp("dump") / "planted.dump"
    token = bench.cluster("token").strip()
    try:
        socket_dir, container = bench.cluster(
            "start", "--", f"shekel-rehearsal-test-{os.getpid()}-{token[:8]}", token,
            bench.image_tag()).splitlines()
        with psycopg.connect(bench.cluster("url", socket_dir, "postgres").strip(),
                             autocommit=True) as connection:
            connection.execute("CREATE DATABASE planted")
        planted = bench.cluster("url", socket_dir, "planted").strip()
        with psycopg.connect(planted, autocommit=True) as connection:
            connection.execute((arcs.REPO / "scripts" / "init_db.sql").read_text(
                encoding="utf-8"))
            connection.execute(
                "CREATE TABLE ledger (id integer PRIMARY KEY, amount numeric(12, 2))")
            connection.execute("INSERT INTO ledger VALUES (1, 12.34), (2, 0.01)")
            connection.execute("CREATE TABLE stamped (id integer PRIMARY KEY, "
                               "created_at timestamptz NOT NULL DEFAULT now())")
            connection.execute("INSERT INTO stamped (id) VALUES (1)")
        with path.open("wb") as archive:
            dumped = bench.in_cluster(container, ["pg_dump", "--format=custom", "--dbname",
                                                  "planted"], stdout=archive)
        assert dumped.returncode == 0, dumped.stderr
    finally:
        bench.cluster("stop", token)
    yield path
