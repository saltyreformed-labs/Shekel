"""Tests for the C-42 migration ``c42b1d9a4e8f``.

The migration restores three salary-schema indexes dropped by
22b3dd9d9ed3 (F-071 / F-079), creates four missing FK-column indexes
(F-139 / F-140), and renames three FK constraints to the project's
``fk_*`` convention (F-072 / F-137 / F-138).  See the migration's
module docstring for the full per-finding rationale.

The test suite exercises five invariants:

  1. **Post-upgrade DB shape.**  Every index named in
     ``INDEX_SPECS`` exists in ``pg_indexes`` after the per-worker
     test-template clone (which carries the migration chain run to
     head).  Every FK in ``FK_RENAME_SPECS`` carries its new name
     and not its old name.
  2. **Idempotency.**  Running ``upgrade()`` against an already-
     migrated DB is a strict no-op (no DDL issued, no DB error,
     every index still present, every FK still under the new name).
  3. **Recreate-on-missing.**  Dropping an index and re-running the
     upgrade restores it with the same shape.  Renaming a FK back
     to its legacy name and re-running the upgrade restores the new
     name.
  4. **DESC ordering preserved.**  The composite
     ``idx_rate_history_account`` index encodes the
     ``effective_date DESC`` sort direction; the post-creation shape
     check refuses to accept an ascending recreation.
  5. **Downgrade round-trip.**  ``downgrade()`` reverses every
     change symmetrically: indexes dropped, FK names restored to
     the pre-C-42 ``<table>_<column>_fkey`` form.  A second
     ``upgrade()`` after the downgrade leaves the DB in the same
     post-upgrade state as the first.

Two additional defensive tests guard the helper invariants:

  6. **Helper return-value contract.**
     ``_create_index_if_missing`` returns True on real DDL, False on
     no-op.  ``_rename_constraint_if_legacy`` does the same.
  7. **Post-rename shape check.**  The migration refuses to silently
     succeed against a DB where the FK was dropped out-of-band
     between the existence check and the rename DDL (verified by
     calling the helper against an explicitly-stripped table).

Tests bootstrap an Alembic ``MigrationContext`` against the test
session's connection so the migration's ``op.create_index`` and the
``op.get_bind`` proxy resolve to real DDL through Alembic's
Operations proxy.  This mirrors the test-c41 round-trip pattern.

Audit reference: F-071, F-072, F-079, F-137, F-138, F-139, F-140 of
shekel-audits/security-2026-04-15/findings.md; commit C-42 of
shekel-audits/security-2026-04-15/remediation-plan.md.
"""
# pylint: disable=redefined-outer-name,unused-argument
# pylint: disable=protected-access
# Rationale: ``redefined-outer-name`` is the canonical pytest fixture
# pattern; tests in this file declare local fixtures and consume them
# under the same names.  ``unused-argument`` is unavoidable for
# fixture-only requests like ``seed_user`` that ensure conftest
# fixtures have produced a populated DB but whose return values the
# assertion does not depend on.  ``protected-access`` is required to
# call the migration's helper functions (prefixed with a leading
# underscore by convention to mark them as private to the migration
# module); the tests exercise these directly to assert their return-
# value contracts.
from __future__ import annotations

import importlib.util
import pathlib
from unittest.mock import patch

import pytest
from sqlalchemy import text

from tests._test_helpers import rewind_paycheck_lines_rename


# ---------------------------------------------------------------------------
# The schema this revision was written against
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _c42_schema(app, db):
    """Run every case here under plan step ``salary:R18-a``'s downgrade.

    Two of the six C-42 indexes and two of its four FK renames live on
    ``salary.paycheck_deductions``, which head renamed to
    ``salary.paycheck_lines`` (ruling **R-SAL38**) along with the kind
    FK's name -- so ``INDEX_SPECS`` / ``FK_RENAME_SPECS``, read straight
    off the migration module, name objects the head template no longer
    carries, and ``upgrade()`` would create an index on a table that is
    not there.  :func:`~tests._test_helpers.rewind_paycheck_lines_rename`
    puts the old names back first (the stacked, newest-first shape of
    :func:`~tests._test_helpers.rewind_pay_schedule_rhythm`); every case
    then reads and drives C-42 against the schema it was written for.
    Nothing is replayed: the ``db`` fixture re-clones the per-worker
    database for every test, and no case here reads a line through the
    models.
    """
    with app.app_context():
        rewind_paycheck_lines_rename(db.session)


# ---------------------------------------------------------------------------
# Migration module loader
# ---------------------------------------------------------------------------


_MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)


def _load_migration(filename: str):
    """Load an Alembic migration file as a Python module via importlib.

    The ``migrations/versions`` directory has no ``__init__.py`` so
    standard ``import`` would fail; alembic itself loads scripts via
    importlib at runtime.  We mirror that behaviour to access module-
    level constants (``INDEX_SPECS``, ``FK_RENAME_SPECS``), helper
    functions, and the ``upgrade`` / ``downgrade`` callables directly.
    """
    path = _MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_M_C42 = _load_migration(
    "c42b1d9a4e8f_c42_repair_salary_indexes_and_fk_naming.py"
)


# ---------------------------------------------------------------------------
# Index / constraint inspection helpers
# ---------------------------------------------------------------------------


def _index_exists(session, schema: str, name: str) -> bool:
    """Return True iff ``schema.name`` is in ``pg_indexes``."""
    return bool(session.execute(text(
        "SELECT EXISTS ("
        "  SELECT 1 FROM pg_indexes "
        "  WHERE schemaname = :schema AND indexname = :name"
        ")"
    ), {"schema": schema, "name": name}).scalar())


def _index_definition(session, schema: str, name: str) -> str | None:
    """Return ``pg_indexes.indexdef`` for the named index or None."""
    return session.execute(text(
        "SELECT indexdef FROM pg_indexes "
        "WHERE schemaname = :schema AND indexname = :name"
    ), {"schema": schema, "name": name}).scalar()


def _constraint_exists(session, schema: str, name: str) -> bool:
    """Return True iff the named constraint exists in ``schema``."""
    return bool(session.execute(text(
        "SELECT EXISTS ("
        "  SELECT 1 FROM pg_constraint cn "
        "  JOIN pg_class c ON c.oid = cn.conrelid "
        "  JOIN pg_namespace n ON n.oid = c.relnamespace "
        "  WHERE cn.conname = :name AND n.nspname = :schema"
        ")"
    ), {"schema": schema, "name": name}).scalar())


def _constraint_definition(session, schema: str, name: str) -> str | None:
    """Return ``pg_get_constraintdef`` for the named constraint, or None.

    Used by the rename round-trip tests to verify the constraint's
    referenced-table / ondelete behavior survived the rename (a
    rename is metadata-only, but the round-trip test asserts that
    invariant against the live catalog rather than trusting the
    PostgreSQL docs in isolation).
    """
    return session.execute(text(
        "SELECT pg_get_constraintdef(cn.oid) "
        "FROM pg_constraint cn "
        "JOIN pg_class c ON c.oid = cn.conrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE cn.conname = :name AND n.nspname = :schema"
    ), {"schema": schema, "name": name}).scalar()


def _drop_index(session, schema: str, name: str) -> None:
    """Drop an index via raw SQL with IF EXISTS guard."""
    session.execute(text(f"DROP INDEX IF EXISTS {schema}.{name}"))


def _rename_constraint_raw(
    session, schema: str, table: str, old_name: str, new_name: str,
) -> None:
    """Issue ALTER TABLE ... RENAME CONSTRAINT via raw SQL.

    Used by the recreate-on-missing test to set up the pre-C-42 state
    (legacy FK name) before running the upgrade.  No idempotency
    check; callers are responsible for ensuring the source name
    exists.
    """
    session.execute(text(
        f"ALTER TABLE {schema}.{table} "
        f"RENAME CONSTRAINT {old_name} TO {new_name}"
    ))


# ---------------------------------------------------------------------------
# Restoration fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def restore_c42_state(db):
    """Ensure every C-42 artifact is restored after the test.

    Several tests in this module drop indexes or rename FKs back to
    their pre-C-42 names to simulate the production-drift state.
    This fixture guarantees the post-C-42 state is back in place
    once the test exits, even if the body raised -- otherwise every
    subsequent C-42 invariant test running in the same per-worker
    DB would silently pass against a degraded schema.

    Cleanup is two-stage:

      1. Drop every C-42 index outright (idempotent via IF EXISTS).
         This clears any malformed recreations from tests that
         deliberately created an ASC-only index to exercise the
         DESC shape check.
      2. Re-run the migration's ``upgrade()`` to recreate every
         index with the canonical shape and rename every FK back
         to the ``fk_*`` form.
    """
    yield
    # pylint: disable=import-outside-toplevel
    from alembic import op
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    db.session.rollback()
    # Stage 1: drop every C-42 index so the canonical recreate can
    # run regardless of the shape the test left the index in.
    for name, schema, _table, _columns in _M_C42.INDEX_SPECS:
        db.session.execute(text(f"DROP INDEX IF EXISTS {schema}.{name}"))
    db.session.commit()

    # Stage 2: re-run upgrade.  Recreates every dropped index and
    # restores any FK that was renamed back to a legacy name.
    ctx = MigrationContext.configure(connection=db.session.connection())
    with Operations.context(ctx):
        with patch.object(
            op, "get_bind",
            return_value=db.session.connection(),
        ):
            _M_C42.upgrade()
    db.session.commit()


# ---------------------------------------------------------------------------
# 1. Post-upgrade DB shape
# ---------------------------------------------------------------------------


class TestPostUpgradeDbShape:
    """The per-worker test DB carries every C-42 artifact by default.

    The test template runs the migration chain to head when built
    (see ``scripts/build_test_template.py``), so every per-worker
    clone inherits the post-C-42 state -- under the R18-a rewind the
    module's autouse fixture applies, which restores the two
    deduction-table artifacts' names.  These tests are the
    template-state precondition for the rest of the suite -- if any
    fail, the template was built without the C-42 migration and
    needs to be rebuilt with
    ``python scripts/build_test_template.py``.
    """

    @pytest.mark.parametrize(
        "name, schema, _table, _columns",
        list(_M_C42.INDEX_SPECS),
        ids=[spec[0] for spec in _M_C42.INDEX_SPECS],
    )
    def test_index_present(
        self, app, db, seed_user, name, schema, _table, _columns,
    ):
        """Each of the six C-42 indexes exists in pg_indexes."""
        with app.app_context():
            assert _index_exists(db.session, schema, name), (
                f"Test template precondition broken: "
                f"{schema}.{name} is missing.  Rebuild the template "
                f"via `python scripts/build_test_template.py`."
            )

    @pytest.mark.parametrize(
        "schema, _table, old_name, new_name",
        list(_M_C42.FK_RENAME_SPECS),
        ids=[spec[3] for spec in _M_C42.FK_RENAME_SPECS],
    )
    def test_fk_renamed(
        self, app, db, seed_user,
        schema, _table, old_name, new_name,
    ):
        """Each of the three C-42 FKs carries the new ``fk_*`` name.

        Two-step assertion: the new name is present AND the legacy
        name is absent.  A partial-rename state (both present, or
        new absent + legacy still there) is reported independently
        so the failure message tells the operator which half of the
        invariant broke.
        """
        with app.app_context():
            new_present = _constraint_exists(db.session, schema, new_name)
            old_present = _constraint_exists(db.session, schema, old_name)
            assert new_present, (
                f"Post-upgrade constraint {schema}.{new_name} is "
                f"missing -- the C-42 rename did not run.  Rebuild "
                f"the template via "
                f"`python scripts/build_test_template.py`."
            )
            assert not old_present, (
                f"Legacy constraint {schema}.{old_name} is still "
                f"present alongside the new {new_name} -- the C-42 "
                f"rename produced a duplicate constraint."
            )

    def test_rate_history_index_has_desc_ordering(
        self, app, db, seed_user,
    ):
        """The composite rate_history index encodes DESC on effective_date.

        The DESC ordering is the load-bearing detail for the
        ``app/routes/loan/escrow_rates.py`` query that filters
        ``WHERE account_id = ? ORDER BY effective_date DESC``.  An
        ascending recreation would still serve correctness (B-tree
        scans backward) but would obscure the canonical query shape.
        """
        with app.app_context():
            definition = _index_definition(
                db.session, "budget", "idx_rate_history_account",
            )
            assert definition is not None, (
                "idx_rate_history_account is missing -- the C-42 "
                "create pass did not run."
            )
            assert " DESC" in definition, (
                f"idx_rate_history_account exists but does not "
                f"encode DESC ordering on effective_date.  Actual "
                f"indexdef: {definition!r}"
            )

    def test_renamed_fks_preserve_ondelete_semantics(
        self, app, db, seed_user,
    ):
        """ALTER ... RENAME CONSTRAINT preserved every FK's ondelete behavior.

        The migration's rename pass is metadata-only -- the column
        list, referenced table, and ondelete behavior must survive
        verbatim.  This test asserts the post-rename
        ``pg_get_constraintdef`` output for each of the three FKs
        carries the expected ondelete clause.

        The three FKs have intentionally different ondelete behaviors:
          * fk_interest_params_account: CASCADE (interest params are
            owned by their account; deleting the account deletes the
            params).
          * fk_transactions_credit_payback_for: RESTRICT since plan step
            ``balance:X-bi-7d-2`` (it was SET NULL through C-42's rename:
            a payback whose source was deleted lost its backlink and
            stood as a zero-link row; ``ck_transactions_one_pricing_link``
            reads ``= 1`` now, so the source cannot go alone -- finding
            **CC-352**'s disclosed window).
          * fk_scenarios_cloned_from: SET NULL (a clone outlives its
            parent).
        """
        expected = {
            ("budget", "fk_interest_params_account"): "ON DELETE CASCADE",
            ("budget", "fk_transactions_credit_payback_for"): "ON DELETE RESTRICT",
            ("budget", "fk_scenarios_cloned_from"): "ON DELETE SET NULL",
        }
        with app.app_context():
            for (schema, name), expected_clause in expected.items():
                definition = _constraint_definition(db.session, schema, name)
                assert definition is not None, (
                    f"Post-rename constraint {schema}.{name} is missing."
                )
                assert expected_clause in definition, (
                    f"Constraint {schema}.{name} lost its ondelete "
                    f"clause through the rename.\n"
                    f"  Expected: {expected_clause!r}\n"
                    f"  Actual:   {definition!r}"
                )


# ---------------------------------------------------------------------------
# 2. Idempotency
# ---------------------------------------------------------------------------


class TestUpgradeIdempotent:
    """``upgrade()`` is a strict no-op against an already-migrated DB.

    The per-worker template comes up post-C-42, so a second upgrade
    must leave every artifact untouched.  Two assertion levels:
    (a) the helper return values report False (no DDL ran), and
    (b) the live catalog still reflects the post-upgrade shape.
    """

    def test_create_index_if_missing_returns_false_for_each_index(
        self, app, db, seed_user,
    ):
        """Every ``_create_index_if_missing`` call returns False.

        False is the no-op signal: the index already exists, no DDL
        was issued.  True would indicate the test template is in an
        unexpected pre-migration state.

        The helper uses ``op.create_index`` on the True path, so the
        call must run inside an Alembic Operations context even
        though the False path never touches ``op``.  Wrapping all
        calls uniformly keeps the test code path identical to the
        helper's production call site.
        """
        # pylint: disable=import-outside-toplevel
        from alembic import op
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        with app.app_context():
            bind = db.session.connection()
            ctx = MigrationContext.configure(connection=bind)
            with Operations.context(ctx):
                with patch.object(op, "get_bind", return_value=bind):
                    for name, schema, table, columns in _M_C42.INDEX_SPECS:
                        result = _M_C42._create_index_if_missing(
                            bind, name, schema, table, columns,
                        )
                        assert result is False, (
                            f"_create_index_if_missing reported True "
                            f"(DDL ran) for {schema}.{name} against a "
                            f"template that should already carry the "
                            f"index."
                        )

    def test_rename_constraint_if_legacy_returns_false_for_each_fk(
        self, app, db, seed_user,
    ):
        """Every ``_rename_constraint_if_legacy`` call returns False.

        False indicates the legacy name is absent -- the FK has
        already been renamed.  True would mean the test template
        carries the pre-C-42 FK name, which would be a template-
        rebuild bug.

        The helper uses ``op.execute`` on the True path, so the
        call must run inside an Alembic Operations context even
        though the False path never touches ``op``.
        """
        # pylint: disable=import-outside-toplevel
        from alembic import op
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        with app.app_context():
            bind = db.session.connection()
            ctx = MigrationContext.configure(connection=bind)
            with Operations.context(ctx):
                with patch.object(op, "get_bind", return_value=bind):
                    for schema, table, old_name, new_name in _M_C42.FK_RENAME_SPECS:
                        result = _M_C42._rename_constraint_if_legacy(
                            bind, schema, table, old_name, new_name,
                        )
                        assert result is False, (
                            f"_rename_constraint_if_legacy reported True "
                            f"(DDL ran) for {schema}.{old_name} -> "
                            f"{new_name} against a template that should "
                            f"already carry the new name."
                        )

    def test_full_upgrade_is_noop_on_already_migrated_db(
        self, app, db, seed_user,
    ):
        """Running ``upgrade()`` against an already-migrated DB succeeds.

        Asserts the round-trip: every artifact present before,
        upgrade() runs without raising, every artifact still present
        after.  Catches a regression where the migration's idempotency
        helpers report no-op but the upgrade() body issues DDL anyway.
        """
        # pylint: disable=import-outside-toplevel
        from alembic import op
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        with app.app_context():
            # Pre-condition: every artifact in place.
            for name, schema, _table, _columns in _M_C42.INDEX_SPECS:
                assert _index_exists(db.session, schema, name)
            for schema, _table, _old, new_name in _M_C42.FK_RENAME_SPECS:
                assert _constraint_exists(db.session, schema, new_name)

            ctx = MigrationContext.configure(
                connection=db.session.connection(),
            )
            with Operations.context(ctx):
                with patch.object(
                    op, "get_bind",
                    return_value=db.session.connection(),
                ):
                    _M_C42.upgrade()
            db.session.commit()

            # Post-condition: every artifact still in place.
            for name, schema, _table, _columns in _M_C42.INDEX_SPECS:
                assert _index_exists(db.session, schema, name), (
                    f"upgrade() dropped {schema}.{name} on a no-op run."
                )
            for schema, _table, old_name, new_name in _M_C42.FK_RENAME_SPECS:
                assert _constraint_exists(db.session, schema, new_name), (
                    f"upgrade() removed {schema}.{new_name} on a no-op run."
                )
                assert not _constraint_exists(db.session, schema, old_name), (
                    f"upgrade() resurrected {schema}.{old_name} on a "
                    f"no-op run."
                )


# ---------------------------------------------------------------------------
# 3. Recreate-on-missing
# ---------------------------------------------------------------------------


class TestUpgradeRecreatesMissingArtifacts:
    """``upgrade()`` restores artifacts that have been removed.

    Simulates the production-drift state F-071 / F-072 documented
    (indexes dropped by 22b3dd9d9ed3, FK left under the legacy name)
    by tearing down each artifact then running the upgrade against
    the otherwise-clean DB.
    """

    @pytest.mark.parametrize(
        "name, schema, _table, _columns",
        list(_M_C42.INDEX_SPECS),
        ids=[spec[0] for spec in _M_C42.INDEX_SPECS],
    )
    def test_upgrade_recreates_each_dropped_index(
        self, app, db, seed_user, restore_c42_state,
        name, schema, _table, _columns,
    ):
        """Dropping an index then upgrading restores it."""
        # pylint: disable=import-outside-toplevel
        from alembic import op
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        with app.app_context():
            _drop_index(db.session, schema, name)
            db.session.commit()
            assert not _index_exists(db.session, schema, name), (
                "Pre-test setup did not drop the index."
            )

            ctx = MigrationContext.configure(
                connection=db.session.connection(),
            )
            with Operations.context(ctx):
                with patch.object(
                    op, "get_bind",
                    return_value=db.session.connection(),
                ):
                    _M_C42.upgrade()
            db.session.commit()

            assert _index_exists(db.session, schema, name), (
                f"upgrade() did not recreate {schema}.{name} after "
                f"it was dropped."
            )

    @pytest.mark.parametrize(
        "schema, table, old_name, new_name",
        list(_M_C42.FK_RENAME_SPECS),
        ids=[spec[3] for spec in _M_C42.FK_RENAME_SPECS],
    )
    def test_upgrade_renames_legacy_fk(
        self, app, db, seed_user, restore_c42_state,
        schema, table, old_name, new_name,
    ):
        """Renaming a FK back to its legacy name and upgrading restores it."""
        # pylint: disable=import-outside-toplevel
        from alembic import op
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        with app.app_context():
            # Set up the legacy-FK state.
            _rename_constraint_raw(
                db.session, schema, table, new_name, old_name,
            )
            db.session.commit()
            assert _constraint_exists(db.session, schema, old_name)
            assert not _constraint_exists(db.session, schema, new_name)

            ctx = MigrationContext.configure(
                connection=db.session.connection(),
            )
            with Operations.context(ctx):
                with patch.object(
                    op, "get_bind",
                    return_value=db.session.connection(),
                ):
                    _M_C42.upgrade()
            db.session.commit()

            assert _constraint_exists(db.session, schema, new_name), (
                f"upgrade() did not rename {schema}.{old_name} -> "
                f"{new_name}."
            )
            assert not _constraint_exists(db.session, schema, old_name), (
                f"upgrade() left {schema}.{old_name} in place after "
                f"the rename."
            )


# ---------------------------------------------------------------------------
# 4. DESC ordering shape check
# ---------------------------------------------------------------------------


class TestRateHistoryDescShapeCheck:
    """The ``_assert_rate_history_index_has_desc`` helper guards DESC.

    Without DESC the loan-rate-change route's
    ``ORDER BY effective_date DESC`` would require a sort step
    despite the index covering both filter and order columns.
    """

    def test_shape_check_passes_for_canonical_index(
        self, app, db, seed_user,
    ):
        """The canonical post-upgrade DB passes the shape check."""
        with app.app_context():
            bind = db.session.connection()
            # No raise = pass.
            _M_C42._assert_rate_history_index_has_desc(bind)

    def test_shape_check_raises_when_index_missing(
        self, app, db, seed_user, restore_c42_state,
    ):
        """The shape check raises RuntimeError if the index is absent.

        Sets up the failure mode the helper guards against: the
        index never materialised (e.g., the create pass silently
        rolled back).  The error message must say "is missing"
        so an operator can recognise the cause.
        """
        with app.app_context():
            _drop_index(
                db.session, "budget", "idx_rate_history_account",
            )
            db.session.commit()

            bind = db.session.connection()
            with pytest.raises(RuntimeError, match="is missing"):
                _M_C42._assert_rate_history_index_has_desc(bind)

    def test_shape_check_raises_when_desc_missing(
        self, app, db, seed_user, restore_c42_state,
    ):
        """The shape check raises if the index is ASC-only.

        Simulates a hand-recreation that forgot the DESC keyword.
        The helper must refuse rather than silently accepting the
        degraded shape.
        """
        with app.app_context():
            _drop_index(
                db.session, "budget", "idx_rate_history_account",
            )
            # Recreate without DESC.
            db.session.execute(text(
                "CREATE INDEX idx_rate_history_account "
                "ON budget.rate_history (account_id, effective_date)"
            ))
            db.session.commit()

            bind = db.session.connection()
            with pytest.raises(RuntimeError, match="does not.*include the DESC"):
                _M_C42._assert_rate_history_index_has_desc(bind)


# ---------------------------------------------------------------------------
# 5. Downgrade round-trip
# ---------------------------------------------------------------------------


class TestDowngradeReversesUpgrade:
    """``downgrade()`` reverses every C-42 change symmetrically."""

    def test_downgrade_drops_every_index(
        self, app, db, seed_user, restore_c42_state,
    ):
        """After downgrade, every C-42 index is absent."""
        # pylint: disable=import-outside-toplevel
        from alembic import op
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        with app.app_context():
            ctx = MigrationContext.configure(
                connection=db.session.connection(),
            )
            with Operations.context(ctx):
                with patch.object(
                    op, "get_bind",
                    return_value=db.session.connection(),
                ):
                    _M_C42.downgrade()
            db.session.commit()

            for name, schema, _table, _columns in _M_C42.INDEX_SPECS:
                assert not _index_exists(db.session, schema, name), (
                    f"downgrade() left {schema}.{name} in place."
                )

    def test_downgrade_restores_legacy_fk_names(
        self, app, db, seed_user, restore_c42_state,
    ):
        """After downgrade, every C-42 FK carries the pre-C-42 name."""
        # pylint: disable=import-outside-toplevel
        from alembic import op
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        with app.app_context():
            ctx = MigrationContext.configure(
                connection=db.session.connection(),
            )
            with Operations.context(ctx):
                with patch.object(
                    op, "get_bind",
                    return_value=db.session.connection(),
                ):
                    _M_C42.downgrade()
            db.session.commit()

            for schema, _table, old_name, new_name in _M_C42.FK_RENAME_SPECS:
                assert _constraint_exists(db.session, schema, old_name), (
                    f"downgrade() did not restore {schema}.{old_name}."
                )
                assert not _constraint_exists(db.session, schema, new_name), (
                    f"downgrade() left {schema}.{new_name} in place."
                )

    def test_full_round_trip_upgrade_downgrade_upgrade(
        self, app, db, seed_user, restore_c42_state,
    ):
        """Upgrade -> downgrade -> upgrade leaves the DB in the post-upgrade state.

        End-to-end round trip: starting from the post-upgrade state,
        run downgrade (now pre-C-42), then upgrade (back to
        post-C-42).  The DB shape after the second upgrade matches
        the original.
        """
        # pylint: disable=import-outside-toplevel
        from alembic import op
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        with app.app_context():
            ctx = MigrationContext.configure(
                connection=db.session.connection(),
            )
            with Operations.context(ctx):
                with patch.object(
                    op, "get_bind",
                    return_value=db.session.connection(),
                ):
                    _M_C42.downgrade()
                    _M_C42.upgrade()
            db.session.commit()

            for name, schema, _table, _columns in _M_C42.INDEX_SPECS:
                assert _index_exists(db.session, schema, name), (
                    f"After round trip, {schema}.{name} is missing."
                )
            for schema, _table, old_name, new_name in _M_C42.FK_RENAME_SPECS:
                assert _constraint_exists(db.session, schema, new_name), (
                    f"After round trip, {schema}.{new_name} is missing."
                )
                assert not _constraint_exists(db.session, schema, old_name), (
                    f"After round trip, legacy {schema}.{old_name} "
                    f"is still present."
                )


# ---------------------------------------------------------------------------
# 6. Helper return-value contracts
# ---------------------------------------------------------------------------


class TestHelperReturnValueContracts:
    """The migration's idempotency helpers return True iff DDL ran."""

    def test_create_index_returns_true_when_creating(
        self, app, db, seed_user, restore_c42_state,
    ):
        """``_create_index_if_missing`` returns True on real DDL.

        Drops the index, calls the helper, asserts True.  The
        boolean drives the regression-test idempotency claim --
        without it tests cannot distinguish "the index was already
        there" from "the helper actually ran the CREATE".
        """
        # pylint: disable=import-outside-toplevel
        from alembic import op
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        with app.app_context():
            _drop_index(db.session, "salary", "idx_deductions_profile")
            db.session.commit()
            bind = db.session.connection()
            ctx = MigrationContext.configure(connection=bind)
            with Operations.context(ctx):
                with patch.object(op, "get_bind", return_value=bind):
                    result = _M_C42._create_index_if_missing(
                        bind, "idx_deductions_profile", "salary",
                        "paycheck_deductions", ["salary_profile_id"],
                    )
            db.session.commit()
            assert result is True, (
                "_create_index_if_missing returned False when the "
                "index was absent -- the helper's True/False contract "
                "is broken."
            )
            assert _index_exists(
                db.session, "salary", "idx_deductions_profile",
            )

    def test_rename_constraint_returns_true_when_renaming(
        self, app, db, seed_user, restore_c42_state,
    ):
        """``_rename_constraint_if_legacy`` returns True on real DDL."""
        # pylint: disable=import-outside-toplevel
        from alembic import op
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        with app.app_context():
            _rename_constraint_raw(
                db.session, "budget", "scenarios",
                "fk_scenarios_cloned_from",
                "scenarios_cloned_from_id_fkey",
            )
            db.session.commit()
            bind = db.session.connection()
            ctx = MigrationContext.configure(connection=bind)
            with Operations.context(ctx):
                with patch.object(op, "get_bind", return_value=bind):
                    result = _M_C42._rename_constraint_if_legacy(
                        bind, "budget", "scenarios",
                        "scenarios_cloned_from_id_fkey",
                        "fk_scenarios_cloned_from",
                    )
            db.session.commit()
            assert result is True, (
                "_rename_constraint_if_legacy returned False when the "
                "legacy name was present -- the helper's True/False "
                "contract is broken."
            )

    def test_rename_constraint_returns_false_when_no_legacy(
        self, app, db, seed_user,
    ):
        """``_rename_constraint_if_legacy`` returns False when the source is absent.

        Confirms the no-op signal.  Calls the helper against a DB
        where the legacy name does not exist (the canonical post-
        upgrade state).  The False path never touches ``op``, but
        the helper API requires a bind regardless.
        """
        # pylint: disable=import-outside-toplevel
        from alembic import op
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext

        with app.app_context():
            bind = db.session.connection()
            ctx = MigrationContext.configure(connection=bind)
            with Operations.context(ctx):
                with patch.object(op, "get_bind", return_value=bind):
                    result = _M_C42._rename_constraint_if_legacy(
                        bind, "budget", "scenarios",
                        "scenarios_cloned_from_id_fkey",
                        "fk_scenarios_cloned_from",
                    )
            assert result is False, (
                "_rename_constraint_if_legacy returned True when the "
                "legacy name was absent -- the helper's True/False "
                "contract is broken."
            )


# ---------------------------------------------------------------------------
# 7. Post-rename shape check refuses missing constraints
# ---------------------------------------------------------------------------


class TestPostRenameShapeCheck:
    """``_assert_constraint_present_post_rename`` refuses degraded states."""

    def test_shape_check_passes_when_new_present_and_old_absent(
        self, app, db, seed_user,
    ):
        """The canonical post-upgrade state passes the shape check."""
        with app.app_context():
            bind = db.session.connection()
            # No raise = pass.
            _M_C42._assert_constraint_present_post_rename(
                bind, "budget",
                new_name="fk_scenarios_cloned_from",
                old_name="scenarios_cloned_from_id_fkey",
            )

    def test_shape_check_raises_when_new_is_missing(
        self, app, db, seed_user,
    ):
        """The shape check raises when the new name is absent.

        Simulates a degraded state where the rename helper believes
        it succeeded but the catalog disagrees -- e.g., the constraint
        was dropped out-of-band.
        """
        with app.app_context():
            bind = db.session.connection()
            with pytest.raises(RuntimeError, match="is missing"):
                _M_C42._assert_constraint_present_post_rename(
                    bind, "budget",
                    new_name="fk_does_not_exist_anywhere",
                    old_name="scenarios_cloned_from_id_fkey",
                )

    def test_shape_check_raises_when_both_present(
        self, app, db, seed_user, restore_c42_state,
    ):
        """The shape check raises when both names exist simultaneously.

        Simulates a state where the rename helper short-circuited
        (legacy absent in its lookup) but a duplicate FK with the
        legacy name was created out-of-band.  This is an ambiguous
        catalog and the helper must refuse to silently accept it.
        """
        with app.app_context():
            # Add a second FK with the legacy name -- on a different
            # column to avoid collision with the existing unique FK.
            # The transactions table has plenty of FK columns; we
            # piggyback on ``template_id`` which has its own ondelete
            # already and won't collide with the canonical
            # transactions_credit_payback_for_id_fkey logic.  (It was
            # ``transfer_id`` until plan step ``balance:X-bi-6-5`` dropped
            # that column; only the catalog entry is read.  The referenced
            # table must be one the seed's idle session holds no lock on,
            # or the ``DROP`` below waits on it: ``budget.categories``
            # timed out, measured 2026-10-27.)
            db.session.execute(text(
                "ALTER TABLE budget.transactions "
                "ADD CONSTRAINT transactions_credit_payback_for_id_fkey "
                "FOREIGN KEY (template_id) "
                "REFERENCES budget.transaction_templates (id) "
                "ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED"
            ))
            db.session.commit()

            try:
                bind = db.session.connection()
                with pytest.raises(RuntimeError, match="is still present"):
                    _M_C42._assert_constraint_present_post_rename(
                        bind, "budget",
                        new_name="fk_transactions_credit_payback_for",
                        old_name="transactions_credit_payback_for_id_fkey",
                    )
            finally:
                db.session.execute(text(
                    "ALTER TABLE budget.transactions "
                    "DROP CONSTRAINT IF EXISTS "
                    "transactions_credit_payback_for_id_fkey"
                ))
                db.session.commit()
