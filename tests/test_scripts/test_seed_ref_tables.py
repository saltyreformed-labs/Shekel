"""``scripts/seed_ref_tables.py`` repairs a database missing a ref row (ruling R-BAL123).

Finding **BAL-536**: the script built the app with a plain ``create_app()``,
whose eager ``ref_cache.init`` refuses a ref table that lacks a row for one of
its enum members -- so the operator's repair tool died on exactly the database
it exists to repair, before its seed ran.  It now builds the app without that
read (``init_ref_cache=False``), seeds, and commits.

**Run as a subprocess under the PRODUCTION config**, because that is the only
config in which the defect exists: the development and testing configs seed
the ref tables inside ``create_app`` itself (``app/__init__.py``'s
``_seed_ref_tables``) before the cache is read, which would mask it.  The
child is pointed at this test's own database, the one the engine is bound to,
with an explicitly EMPTY ``DATABASE_URL_APP`` (the config resolver reads it as
unset) so no other database can be reached.
"""
from __future__ import annotations

import os
import pathlib
import secrets
import subprocess
import sys

from sqlalchemy import text

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "seed_ref_tables.py"


def _production_env(database_url: str) -> dict:
    """Build the child's environment: the production config, on *database_url*.

    Only what Python needs to run is inherited.  ``ProdConfig`` refuses to
    start without a SECRET_KEY of real length and a non-memory rate-limit
    store; neither is contacted by the seed.

    Args:
        database_url: The URL of this test's database, password included.

    Returns:
        The environment mapping for :func:`subprocess.run`.
    """
    env = {
        key: os.environ[key]
        for key in ("PATH", "PYTHONPATH", "PYTHONHOME", "HOME", "VIRTUAL_ENV",
                    "LANG", "LC_ALL")
        if key in os.environ
    }
    env.update({
        "FLASK_ENV": "production",
        "DATABASE_URL": database_url,
        "DATABASE_URL_APP": "",
        "SECRET_KEY": secrets.token_hex(32),
        "RATELIMIT_STORAGE_URI": "valkey://127.0.0.1:6379/0",
    })
    return env


class TestTheRepairToolRepairs:
    """The tool seeds a missing row instead of dying on it."""

    def test_a_missing_ref_row_is_seeded_back_and_committed(self, app, db):
        """A deleted enum row is inserted again, and the tool exits 0.

        ``ref.statement_sources`` 'secu_checking_csv' backs an enum member the
        strict cache requires, and nothing in a per-test database references
        it.  Before ruling R-BAL123 the child exited 1 with ``ref_cache.init()
        failed -- ... StatementSource.SECU_CHECKING_CSV``.
        """
        db.session.execute(text(
            "DELETE FROM ref.statement_sources WHERE name = 'secu_checking_csv'"
        ))
        db.session.commit()

        result = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            env=_production_env(
                db.engine.url.render_as_string(hide_password=False),
            ),
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=25,
            check=False,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert "+ statement_sources: secu_checking_csv" in result.stdout
        with db.engine.connect() as conn:
            assert conn.execute(text(
                "SELECT count(*) FROM ref.statement_sources "
                "WHERE name = 'secu_checking_csv'"
            )).scalar_one() == 1
