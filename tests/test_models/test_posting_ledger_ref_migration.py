"""Tests for the f5037400dc5e posting-ledger ref-tables migration.

Build-Order Step 2, Commit 1
(``docs/audits/balance_architecture/implementation_plan_posting_ledger_transfers.md``).
The migration creates the three ``ref`` lookup tables
(``ledger_account_classes``, ``posting_kinds``, ``posting_sources``) and
inline-seeds the Step-2 values.

The migration is already at HEAD when these tests run (the template
builder upgraded it base->head), so the per-worker DB shows the
post-migration state.  These tests therefore assert, without re-executing
DDL in the worker:

  * the migration is correctly chained (revision / down_revision);
  * the three ``ref`` lookup tables exist and ``ledger_account_classes``
    carries exactly its five classes with the correct ``is_debit_normal``
    flags (f5037400dc5e is the sole, permanent producer of that table --
    accounting has exactly five classes);
  * the Step-2 ``transfer`` kind is present.  Later Build-Order steps add
    more rows to ``posting_kinds`` / ``posting_sources`` via their own
    migrations (Step 3's ``income`` / ``expense`` / ``transaction``), so
    this file asserts only f5037400dc5e's own contribution; the cumulative
    inline-seed coverage for every enum member lives in
    ``test_posting_ref_seed_parity.py``.  The ``transfer`` SOURCE it seeds
    beside the kind is gone at HEAD: migration ``1712c506ee99`` (plan step
    ``balance:X-bi-6-5``'s second leaf, card plan#1845) deleted it, and its
    own suite proves that;
  * the ``downgrade`` is not a bare pass -- it drops every table the
    ``upgrade`` materialises.

A full executable upgrade -> downgrade -> upgrade round-trip belongs in
the Alembic-driven environment, not an in-test xdist worker: a
``DROP TABLE`` needs an ACCESS EXCLUSIVE lock that conflicts with the
ACCESS SHARE locks the framework's session-scoped ``ref_cache`` refresh
holds on these ref tables, so it dies on ``lock_timeout``.  This is the
same constraint that drove the ``loan_anchor_events`` migration test
(``tests/test_models/test_loan_anchor_backfill.py::TestDowngradeSmoke``)
to a source-level downgrade check.  The executable round-trip was run
manually against the prod-clone dev DB during development (downgrade
dropped all three tables and their owned sequences cleanly; re-upgrade
restored f5037400dc5e's five ledger classes and the ``transfer`` kind and
source it seeds).
"""
from __future__ import annotations

import importlib.util
import pathlib

from sqlalchemy import text


# ---------------------------------------------------------------------------
# Migration module loader -- importlib pattern from the C-43 / loan-anchor
# migration tests (migrations/versions has no __init__.py).
# ---------------------------------------------------------------------------


_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)

_MIGRATION_FILENAME = (
    "f5037400dc5e_add_posting_ledger_ref_tables_ledger_.py"
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
    """The migration chains off the recorded head."""

    def test_revision_pair(self):
        """revision / down_revision pin the migration into the chain."""
        assert _MIGRATION.revision == "f5037400dc5e"
        assert _MIGRATION.down_revision == "b483e2b8a6d2"


class TestMigratedAndSeededState:
    """The HEAD per-worker DB carries the exact Step-2 schema and seed.

    These assertions read the live migrated state the template build
    produced by running ``upgrade`` -- the upgrade+seed contract proven
    against the real migrated schema rather than a re-execution.
    """

    def test_all_three_tables_exist(self, app, db):
        """All three posting-ledger ref tables are present at HEAD."""
        with app.app_context():
            present = {
                row[0] for row in db.session.execute(text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'ref' AND table_name IN "
                    "('ledger_account_classes', 'posting_kinds', "
                    "'posting_sources')"
                )).fetchall()
            }
            assert present == {
                "ledger_account_classes",
                "posting_kinds",
                "posting_sources",
            }, present

    def test_ledger_account_classes_seeded_with_correct_flags(self, app, db):
        """Every seeded class carries the correct is_debit_normal.

        Asset and Expense are debit-normal; Liability, Income, Equity and
        Unrealized are credit-normal.  Read straight from the migrated table
        so each migration's inline seed is validated independently of the ORM
        / ref_cache layer.

        **The assertion stays an EXACT row set where its posting-kind sibling
        below uses membership**, and the difference is deliberate: a class
        decides how EVERY account in it is presented (``is_debit_normal`` is
        what a reader negates by), and there are six of them, so an
        unannounced seventh is worth failing on.  ``Unrealized`` joined at plan
        step X-f3d, migration ``e6b4a2d8c713`` (ruling **R-FO**): other
        comprehensive income, credit-normal because a gain is a credit.
        """
        with app.app_context():
            rows = dict(db.session.execute(text(
                "SELECT name, is_debit_normal "
                "FROM ref.ledger_account_classes"
            )).fetchall())
            assert rows == {
                "Asset": True,
                "Liability": False,
                "Income": False,
                "Expense": True,
                "Equity": False,
                "Unrealized": False,
            }, rows

    def test_posting_kinds_and_sources_seeded(self, app, db):
        """f5037400dc5e seeds the Step-2 ``transfer`` kind (its source is gone at HEAD).

        The ``transfer`` source it seeded beside the kind was deleted by
        migration ``1712c506ee99`` (the module docstring); the kind stays,
        carried by a transfer movement's legs.
        Asserts membership rather than the exact row set: later Build-Order
        steps add more kinds/sources (Step 3's ``income`` / ``expense``
        kinds and ``transaction`` source) via their own migrations, each
        asserted in that migration's own test
        (``test_posting_cash_ref_migration.py``).  The cumulative
        inline-seed coverage for every enum member is guarded by
        ``test_posting_ref_seed_parity.py``.
        """
        with app.app_context():
            kinds = {
                row[0] for row in db.session.execute(text(
                    "SELECT name FROM ref.posting_kinds"
                )).fetchall()
            }
            assert "transfer" in kinds, kinds


class TestDowngradeReversible:
    """downgrade() is a real revert, not a bare pass.

    A source-level check (the executable round-trip is out of scope for
    the xdist worker -- see the module docstring) guards against a future
    edit silently re-routing the downgrade past one of the artefacts the
    upgrade materialises, which would leave a half-reverted schema -- the
    bare-pass downgrade failure mode the coding standard explicitly
    forbids.
    """

    def test_downgrade_drops_all_three_tables(self):
        """The downgrade source drops every table the upgrade creates."""
        source = (_MIGRATIONS_DIR / _MIGRATION_FILENAME).read_text()
        for table in (
            "ledger_account_classes",
            "posting_kinds",
            "posting_sources",
        ):
            assert (
                f'drop_table("{table}"' in source
                or f"drop_table('{table}'" in source
            ), f"downgrade() never drops ref.{table}"
