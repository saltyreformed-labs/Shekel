"""align system.audit_log with canonical schema: executed_at NOT NULL + ck_audit_log_operation

Closes H-4 of shekel-audits/security-2026-04-15/model-migration-drift.md.
Two storage-tier guarantees declared in
``app/audit_infrastructure.py:_CREATE_AUDIT_LOG_TABLE_SQL`` -- the
canonical CREATE TABLE used by the test path -- never reached the
production audit_log table because the original migration
``a8b1c2d3e4f5_add_audit_log_and_triggers.py`` predated both
additions and the rebuild migration ``a5be2a99ea14`` uses
``CREATE TABLE IF NOT EXISTS`` so it leaves an existing table
untouched:

  * ``executed_at`` is canonically ``NOT NULL DEFAULT now()`` but the
    migration-built column is nullable.
    ``8a21d16c9bde_tighten_audit_timestamp_nullability_`` deliberately
    limited its sweep to ``user-facing schemas`` and skipped
    ``system``.
  * ``ck_audit_log_operation`` (``CHECK operation IN ('INSERT',
    'UPDATE', 'DELETE')``) is missing from the migration-built table
    entirely.

The trigger function always populates ``executed_at`` via the
``DEFAULT now()`` clause and always emits a TG_OP value in the
INSERT/UPDATE/DELETE set, so practical impact is nil today -- but a
future caller that bypasses the trigger (a future raw-SQL INSERT, a
``pg_dump`` reload that omits the column, an off-trigger forensic
backfill, a buggy script that writes ``TRUNCATE`` as the operation)
would corrupt the forensic trail under one column or the other.
Symmetric storage-tier guarantees across the create_all and
migration paths close both gaps.

Pre-flight detection refuses the upgrade if any pre-existing row
violates either predicate -- should be impossible given the trigger
contract, but cheap to verify.  If the assertion ever trips, an
unknown writer has been bypassing the trigger and the operator
needs to investigate before constraining the column.

Revision ID: b2b1ff4c3cea
Revises: 1702cadcae54
Create Date: 2026-05-10
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "b2b1ff4c3cea"
down_revision = "1702cadcae54"
branch_labels = None
depends_on = None


def _constraint_exists(name: str, schema: str) -> bool:
    """Return True iff a constraint named ``name`` exists in ``schema``.

    Mirrors the ``_constraint_exists`` helper in
    ``22b3dd9d9ed3_add_salary_schema_tables.py``.  Used to make the
    ck_audit_log_operation ADD CONSTRAINT idempotent against a
    database that already carries the constraint -- the test path
    materialises it via ``apply_audit_infrastructure`` on a fresh
    database that lacks the table entirely.
    """
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM pg_constraint c "
            "JOIN pg_namespace n ON c.connamespace = n.oid "
            "WHERE c.conname = :name AND n.nspname = :schema"
        ),
        {"name": name, "schema": schema},
    )
    return result.fetchone() is not None


def upgrade():
    """Pre-flight check for violators, then tighten executed_at and add the operation CHECK.

    Two pre-flight queries refuse the upgrade if any pre-existing row
    would violate the new constraints.  When both pass, the DDL phase:

    1. ALTERs ``system.audit_log.executed_at`` to NOT NULL.  No-op on
       a column that is already NOT NULL.
    2. Adds ``ck_audit_log_operation`` if not already present.

    Idempotency lets the migration run cleanly against the test path
    (``db.create_all()`` + ``apply_audit_infrastructure`` already
    produces NOT NULL and the CHECK) and tightens the production
    columns on the migration path.
    """
    bind = op.get_bind()

    null_executed_at = bind.execute(
        sa.text(
            "SELECT count(*) FROM system.audit_log "
            "WHERE executed_at IS NULL"
        )
    ).scalar()
    if null_executed_at and null_executed_at > 0:
        raise RuntimeError(
            f"Refusing to set system.audit_log.executed_at NOT NULL: "
            f"{null_executed_at} existing row(s) have NULL executed_at. "
            f"Resolve before retrying (typically by setting executed_at "
            f"to the row's logical time-of-write -- the id ordering "
            f"can guide an interpolated backfill -- and confirm with "
            f"the user before any UPDATE)."
        )

    bad_operation = bind.execute(
        sa.text(
            "SELECT count(*) FROM system.audit_log "
            "WHERE operation NOT IN ('INSERT', 'UPDATE', 'DELETE')"
        )
    ).scalar()
    if bad_operation and bad_operation > 0:
        raise RuntimeError(
            f"Refusing to add ck_audit_log_operation: "
            f"{bad_operation} existing row(s) carry an operation "
            f"outside ('INSERT', 'UPDATE', 'DELETE').  The trigger "
            f"function emits only TG_OP values from that set, so a "
            f"non-matching row indicates a writer that bypassed the "
            f"trigger.  Investigate (`SELECT DISTINCT operation, "
            f"count(*) FROM system.audit_log GROUP BY operation`) "
            f"before constraining the column."
        )

    op.alter_column(
        "audit_log",
        "executed_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        existing_server_default=sa.text("now()"),
        schema="system",
    )

    if not _constraint_exists("ck_audit_log_operation", "system"):
        op.create_check_constraint(
            "ck_audit_log_operation",
            "audit_log",
            "operation IN ('INSERT', 'UPDATE', 'DELETE')",
            schema="system",
        )


def downgrade():
    """Drop the CHECK constraint and restore nullable executed_at.

    Reverse order of the upgrade: the CHECK is dropped first, then the
    column relaxed to nullable.  Raw ``ALTER TABLE ... DROP CONSTRAINT
    IF EXISTS`` makes the CHECK drop a no-op on a database that did
    not carry it before the upgrade (the create_check_constraint guard
    above would have skipped the add).
    """
    op.execute(
        "ALTER TABLE system.audit_log "
        "DROP CONSTRAINT IF EXISTS ck_audit_log_operation"
    )
    op.alter_column(
        "audit_log",
        "executed_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
        existing_server_default=sa.text("now()"),
        schema="system",
    )
