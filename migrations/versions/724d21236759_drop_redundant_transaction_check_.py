"""drop redundant ck_transactions_positive_* CHECK constraints

Closes H-1 of shekel-audits/security-2026-04-15/model-migration-drift.md.
The constraints ck_transactions_positive_amount and
ck_transactions_positive_actual were added by
c5d6e7f8a901_add_positive_amount_check_constraints.py with the same
predicates as ck_transactions_estimated_amount and
ck_transactions_actual_amount (added later in
dc46e02d15b4_add_check_constraints_to_loan_params_.py and the only
pair declared in app/models/transaction.py).  The duplicate pair is
dead weight: identical validation logic, twice the constraint check
on every INSERT/UPDATE, and the names diverge from the model so
db.create_all() (the test suite path) does not produce them.

Dropping the duplicates aligns the live database with the model and
keeps a single name pinned to each predicate.

Revision ID: 724d21236759
Revises: d477228fee56
Create Date: 2026-05-10
"""
from alembic import op


# Revision identifiers, used by Alembic.
revision = "724d21236759"
down_revision = "d477228fee56"
branch_labels = None
depends_on = None


def upgrade():
    """Drop the redundant duplicate CHECK constraints.

    Both predicates remain enforced by the surviving
    ck_transactions_estimated_amount and ck_transactions_actual_amount
    constraints declared in app/models/transaction.py, so removing the
    duplicates does not relax any validation guarantee.

    IF EXISTS guards are required because production (and any
    developer DB initialised via scripts/init_database.py +
    db.create_all() + Alembic stamp) never ran the c5d6e7f8a901
    migration that created these constraints -- the duplicates only
    materialise in databases built end-to-end via flask db upgrade.
    The drop must therefore tolerate their absence; the migration is
    a no-op on those databases and a real cleanup on a fresh-from-
    migrations DB (the per-pytest-worker template path).  Raw
    ALTER TABLE ... DROP CONSTRAINT IF EXISTS is used because
    op.drop_constraint() has no IF EXISTS knob.
    """
    op.execute(
        "ALTER TABLE budget.transactions "
        "DROP CONSTRAINT IF EXISTS ck_transactions_positive_actual"
    )
    op.execute(
        "ALTER TABLE budget.transactions "
        "DROP CONSTRAINT IF EXISTS ck_transactions_positive_amount"
    )


def downgrade():
    """Re-add the duplicate CHECK constraints.

    Restores the pre-upgrade schema state on a database that carried
    the duplicates before the upgrade.  Symmetric to the IF EXISTS
    drop in upgrade(): on a database that never carried them, the
    downgrade re-introduces them, which matches the documented
    pre-upgrade schema rather than the natural-history schema of
    that particular database.  This is acceptable because the
    duplicates are functionally equivalent to the surviving
    constraints; downgrade restores the constraint set the
    migration removed.
    """
    op.create_check_constraint(
        "ck_transactions_positive_amount",
        "transactions",
        "estimated_amount >= 0",
        schema="budget",
    )
    op.create_check_constraint(
        "ck_transactions_positive_actual",
        "transactions",
        "actual_amount IS NULL OR actual_amount >= 0",
        schema="budget",
    )
