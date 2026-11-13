"""The helpers and constants every part of this package shares: the module-level helpers of
``tests/test_models/test_c43_ondelete_and_naming_convention.py``, moved verbatim by
``tools/split``.
"""

from __future__ import annotations

import importlib.util

from sqlalchemy import text

from tests._repo_root import REPO_ROOT


# ---------------------------------------------------------------------------
# Migration module loader
# ---------------------------------------------------------------------------


_MIGRATIONS_DIR = REPO_ROOT / "migrations" / "versions"


def _load_migration(filename: str):
    """Load an Alembic migration file as a Python module via importlib.

    The ``migrations/versions`` directory has no ``__init__.py`` so
    a standard ``import`` would fail; alembic itself loads scripts
    via importlib at runtime.  We mirror that behaviour to access
    module-level constants (``REF_FK_SPECS``,
    ``TRANSFERS_PAY_PERIOD_SPEC``), helper functions, and the
    ``upgrade`` / ``downgrade`` callables directly.
    """
    path = _MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_M_C43 = _load_migration(
    "b4b588a49a0c_c43_fk_ondelete_restrict_and_naming_.py"
)


# ---------------------------------------------------------------------------
# Catalog inspection helpers
# ---------------------------------------------------------------------------


def _constraint_exists(session, schema: str, name: str) -> bool:
    """Return True iff the named constraint exists in ``schema``.

    Mirrors the migration's ``_constraint_exists`` helper so the test
    assertions read against the same catalog view the production
    code does.
    """
    return bool(session.execute(text(
        "SELECT EXISTS ("
        "  SELECT 1 FROM pg_constraint cn "
        "  JOIN pg_class c ON c.oid = cn.conrelid "
        "  JOIN pg_namespace n ON n.oid = c.relnamespace "
        "  WHERE cn.conname = :name AND n.nspname = :schema"
        ")"
    ), {"schema": schema, "name": name}).scalar())


def _fk_ondelete_code(session, schema: str, name: str) -> str | None:
    """Return ``pg_constraint.confdeltype`` for the named FK, or None.

    Reads the raw single-character code (``a`` = NO ACTION, ``r`` =
    RESTRICT, ``c`` = CASCADE, ``n`` = SET NULL).  Callers assert the
    letter itself, PostgreSQL's own spelling, so the oracle is the
    catalog's and never the migration's table under test (ruling
    plan#3737); the raw code is returned here so the test can detect
    "constraint absent" (None) vs "constraint present with action X"
    in a single read.
    """
    return session.execute(text(
        "SELECT cn.confdeltype FROM pg_constraint cn "
        "JOIN pg_class c ON c.oid = cn.conrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE cn.conname = :name AND n.nspname = :schema "
        "AND cn.contype = 'f'"
    ), {"schema": schema, "name": name}).scalar()


def _force_fk_to_legacy_state(
    session,
    source_schema: str,
    source_table: str,
    source_column: str,
    target_schema: str,
    target_table: str,
    target_column: str,
    legacy_name: str,
    new_name: str,
    legacy_ondelete: str,
) -> None:
    """Tear the FK back to its pre-C-43 shape for the recreate-on-drift tests.

    Drops the current (new-name, new-ondelete) constraint and adds
    one under the legacy name with the legacy ondelete clause.  The
    helper is the test-side mirror of
    ``_M_C43.drop_and_recreate_fk`` -- we issue raw DDL here
    instead of calling the helper backwards so the test stays
    independent of the helper's API.

    Args:
        session: SQLAlchemy session bound to the test DB.
        source_schema/source_table/source_column: FK's referencing
            side.
        target_schema/target_table/target_column: FK's referenced
            side.
        legacy_name: Constraint name to install.
        new_name: Current constraint name to drop.
        legacy_ondelete: SQL keyword for the legacy ondelete clause.
    """
    session.execute(text(
        f"ALTER TABLE {source_schema}.{source_table} "
        f"DROP CONSTRAINT IF EXISTS {new_name}"
    ))
    session.execute(text(
        f"ALTER TABLE {source_schema}.{source_table} "
        f"ADD CONSTRAINT {legacy_name} "
        f"FOREIGN KEY ({source_column}) "
        f"REFERENCES {target_schema}.{target_table} ({target_column}) "
        f"ON DELETE {legacy_ondelete}"
    ))
