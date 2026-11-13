"""What the stub cannot model, run on real code: init_database's migrate raises an upgrade failure,
and the script's stamp read waits on real PostgreSQL for a step 3 still ending.
"""

from __future__ import annotations

import threading
import time
from unittest import mock

import pytest
from sqlalchemy import text

from ._helpers import _the_script_s_stamp_read


class TestInitDatabaseStillRaises:
    """R-R14's other prohibition, tested by behaviour rather than by shape.

    The earlier source-level version walked the AST for a ``Try`` containing
    the ``command.upgrade`` call.  Review found five wrappings it missed --
    ``except*`` (which parses to ``ast.TryStar``), ``contextlib.suppress``, a
    decorator, a nested function, and a try at the CALL site -- and two
    harmless shapes it wrongly rejected (``try/finally`` with no handler, and
    ``try/except: raise``).  Asking whether the exception actually escapes has
    none of those gaps.
    """

    def test_an_upgrade_failure_propagates_out_of_migrate(self, monkeypatch):
        """``migrate_existing_database`` does not swallow an Alembic error.

        This is the failure mode F-8 turns on: an unresolvable revision must
        abort the deploy loudly rather than let the app boot against a schema
        it cannot describe.
        """
        # Pylint: ``import-outside-toplevel`` -- the module is loaded by path
        # (scripts/ is not a package), and only this test needs it.
        # pylint: disable=import-outside-toplevel
        from app import migration_runner
        from tests._test_helpers import load_init_database_module

        module = load_init_database_module()

        def _boom(*_args, **_kwargs):
            """Stand in for a revision Alembic cannot locate."""
            raise RuntimeError("Can't locate revision identified by 'deadbeef'")

        # Alembic's own ``upgrade``, which the one migration runner calls, so
        # the error starts where a real one would.  It raises before the
        # config's connection is used, so a stand-in that is already inside a
        # transaction (the one question the runner asks of it) takes its place.
        monkeypatch.setattr(migration_runner.command, "upgrade", _boom)
        in_a_transaction = mock.Mock(**{"in_transaction.return_value": True})
        with pytest.raises(RuntimeError, match="Can't locate revision"):
            module.migrate_existing_database(in_a_transaction)


class TestTheStampReadWaitsForAStep3StillEnding:
    """Plan step balance:X-cv, ruling R-BAL115: the re-read's SQL, on real PostgreSQL.

    The stub above can pin only how the stamp question is SPELLED.  This runs
    the script's own statement against the test database while another
    connection holds an uncommitted stamp UPDATE -- the state a stopped
    container's entrypoint step 3 leaves when its COMMIT is still on the
    wire -- and measures that the read WAITS for it (PostgreSQL reports the
    reader blocked on a lock) and then returns the COMMITTED stamp, not the
    one it would have read past.
    """

    def test_the_read_waits_then_returns_what_step_3_committed(self, app, db):
        """Blocked while the UPDATE is open; the new stamp once it commits."""
        engine = db.engine
        sql = _the_script_s_stamp_read()
        read = {}

        def _read_the_stamp():
            """Run the script's read on a connection of its own."""
            with engine.connect() as reader:
                read["stamp"] = reader.execute(text(sql)).scalar_one()

        with engine.connect() as step_3, engine.connect() as watcher:
            step_3.execute(text(
                "UPDATE public.alembic_version SET version_num = 'xcvwaits0001'"
            ))
            reader_thread = threading.Thread(target=_read_the_stamp)
            reader_thread.start()
            deadline = time.monotonic() + 10
            while not watcher.execute(text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() "
                "  AND pid <> pg_backend_pid() AND wait_event_type = 'Lock' "
                "  AND query LIKE '%public.alembic_version%'"
            )).scalar_one():
                watcher.rollback()
                assert time.monotonic() < deadline, (
                    f"the stamp read never waited on step 3's lock; it read "
                    f"{read.get('stamp')!r} past it"
                )
                time.sleep(0.05)
            assert "stamp" not in read
            step_3.commit()
            reader_thread.join(timeout=10)

        assert not reader_thread.is_alive()
        assert read["stamp"] == "xcvwaits0001"
