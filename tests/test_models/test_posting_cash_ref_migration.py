"""Tests for the 97bc03c2aa4c cash-posting ref-values migration.

Build-Order Step 3, Commit 1
(``docs/audits/balance_architecture/implementation_plan_posting_ledger_cash_envelopes.md``).
The migration adds reference VALUES (not tables) to two ``ref`` lookup
tables Step 2 created: the ``income`` / ``expense`` kinds to
``ref.posting_kinds`` and the ``transaction`` source to
``ref.posting_sources``.

The migration is already at HEAD when these tests run (the template builder
upgraded it base->head), so the per-worker DB shows the post-migration
state.  These tests assert, without re-executing DML in the worker:

  * the migration is correctly chained (revision / down_revision);
  * this migration's own surviving contribution is present in the live
    tables (the ``income`` / ``expense`` kinds) -- the Step-2 ``transfer``
    kind is asserted by f5037400dc5e's own test, and the cumulative
    inline-seed coverage for every enum member lives in
    ``test_posting_ref_seed_parity.py``.  Its ``transaction`` source is GONE
    at HEAD: migration ``1712c506ee99`` (plan step ``balance:X-bi-6-5``'s
    second leaf, card plan#1845) deleted it, and its own suite proves that;
  * the ``downgrade`` is not a bare pass -- it deletes exactly the three
    rows the ``upgrade`` adds.

A full executable upgrade -> downgrade -> upgrade round-trip belongs in the
Alembic-driven environment, not an in-test xdist worker.  Even though this
migration's downgrade is a row ``DELETE`` (not a ``DROP TABLE``, so it
needs no ACCESS EXCLUSIVE lock), mutating these ``ref`` rows inside a
worker would desync the framework's session-scoped ``ref_cache`` -- it is
initialised once per session from these exact rows, and every other test
in the worker reads the cached IDs -- so the source-level downgrade check
is the safe analogue of the one used for f5037400dc5e and the loan-anchor
migration.  The executable round-trip was run manually against the
prod-clone dev DB during development (downgrade removed the three rows;
re-upgrade restored them identically).
"""
from __future__ import annotations

import importlib.util
import pathlib

from sqlalchemy import text


# ---------------------------------------------------------------------------
# Migration module loader -- importlib pattern from the f5037400dc5e /
# loan-anchor migration tests (migrations/versions has no __init__.py).
# ---------------------------------------------------------------------------


_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)

_MIGRATION_FILENAME = (
    "97bc03c2aa4c_add_cash_posting_kinds_and_transaction_.py"
)


def _load_migration(filename):
    """Load an Alembic migration module by path via importlib."""
    path = _MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_MIGRATION = _load_migration(_MIGRATION_FILENAME)


class TestMigrationRevisionPair:
    """The migration chains off the Step-2 head."""

    def test_revision_pair(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "97bc03c2aa4c"
        assert _MIGRATION.down_revision == "db239773c2fd"


class TestMigratedState:
    """The HEAD per-worker DB carries this migration's new reference rows.

    Reads the live migrated state the template build produced -- the
    upgrade contract proven against the real migrated schema rather than a
    re-execution.  Asserts membership (this migration's own contribution),
    not the exact row set, since f5037400dc5e and later steps also populate
    these tables.
    """

    def test_cash_posting_kinds_present(self, app, db):
        """``income`` and ``expense`` kinds are seeded at HEAD."""
        with app.app_context():
            kinds = {
                row[0] for row in db.session.execute(text(
                    "SELECT name FROM ref.posting_kinds"
                )).fetchall()
            }
            assert {"income", "expense"} <= kinds, kinds


class TestDowngradeReversible:
    """downgrade() is a real revert, not a bare pass.

    A source-level check (the executable round-trip is out of scope for the
    xdist worker -- see the module docstring) guards against a future edit
    silently dropping one of the deletes the upgrade's three inserts
    require, which would leave orphaned reference rows on a downgrade -- the
    bare-pass downgrade failure mode the coding standard forbids.
    """

    def test_downgrade_deletes_every_added_row(self):
        """The downgrade source deletes each row the upgrade adds.

        The value check is scoped to the text from the first ``DELETE FROM``
        onward (the downgrade constants and ``downgrade()`` follow the
        upgrade seed constants in the module) so a value is credited only if
        it is named in a DELETE -- not merely echoed by the upgrade's
        earlier INSERTs.
        """
        source = (_MIGRATIONS_DIR / _MIGRATION_FILENAME).read_text()
        assert "DELETE FROM ref.posting_kinds" in source, (
            "downgrade() never deletes from ref.posting_kinds"
        )
        assert "DELETE FROM ref.posting_sources" in source, (
            "downgrade() never deletes from ref.posting_sources"
        )
        delete_section = source[source.find("DELETE FROM"):]
        for value in ("income", "expense", "transaction"):
            assert f"'{value}'" in delete_section, (
                f"downgrade() never names the '{value}' row to delete"
            )
