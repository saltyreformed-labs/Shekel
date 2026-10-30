"""add ck_recurrence_rules_dom and ck_recurrence_rules_moy CHECK constraints

Closes H-3 of shekel-audits/security-2026-04-15/model-migration-drift.md.
The model app/models/recurrence_rule.py declares three CHECK
constraints on budget.recurrence_rules:

  * ck_recurrence_rules_dom: day_of_month IS NULL OR (1..31)
  * ck_recurrence_rules_due_dom: due_day_of_month IS NULL OR (1..31)
  * ck_recurrence_rules_moy: month_of_year IS NULL OR (1..12)

The migration chain materialises only ck_recurrence_rules_due_dom (via
f15a72a3da6c_add_due_date_paid_at_to_transactions_.py).  The other two
were never added to a migration, so production has been accepting
out-of-range values (day_of_month=99, month_of_year=15) that the
recurrence engine would translate into impossible dates -- silently
generating transactions on dates that do not exist and corrupting
balance projections downstream.

Pre-flight detection refuses the upgrade when any pre-existing row
violates a predicate; the operator resolves by editing the offending
row and re-runs.  Mirrors the C-24 sweep pattern in
b71c4a8f5d3e_c24_marshmallow_range_check_sweep.py -- per
docs/coding-standards.md the migration never auto-rewrites data.

Revision ID: 1702cadcae54
Revises: 724d21236759
Create Date: 2026-05-10
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = "1702cadcae54"
down_revision = "724d21236759"
branch_labels = None
depends_on = None


# Each entry: (constraint_name, predicate, detection_sql).
#
# ``predicate`` is the SQL CHECK body; ``detection_sql`` returns the
# offending rows when the predicate would FAIL.  Same shape as the
# C-24 sweep so a future operator who has read that migration knows
# exactly what to expect here.
_CHECK_SPECS = [
    (
        "ck_recurrence_rules_dom",
        "day_of_month IS NULL OR (day_of_month >= 1 AND day_of_month <= 31)",
        (
            "SELECT id, user_id, day_of_month "
            "FROM budget.recurrence_rules "
            "WHERE day_of_month IS NOT NULL "
            "  AND (day_of_month < 1 OR day_of_month > 31) "
            "ORDER BY id"
        ),
    ),
    (
        "ck_recurrence_rules_moy",
        "month_of_year IS NULL OR (month_of_year >= 1 AND month_of_year <= 12)",
        (
            "SELECT id, user_id, month_of_year "
            "FROM budget.recurrence_rules "
            "WHERE month_of_year IS NOT NULL "
            "  AND (month_of_year < 1 OR month_of_year > 12) "
            "ORDER BY id"
        ),
    ),
]


def _constraint_exists(name: str, schema: str) -> bool:
    """Return True iff a constraint named ``name`` exists in ``schema``.

    Mirrors the ``_constraint_exists`` helper in
    ``22b3dd9d9ed3_add_salary_schema_tables.py``.  Used to make the
    DDL phase idempotent against a database that was bootstrapped via
    ``db.create_all()`` (which materialises the inline
    ``db.CheckConstraint`` declarations on the model) rather than via
    the migration chain.  Production and developer DBs follow the
    create_all path per ``scripts/init_database.py``, so the
    constraints already exist on those targets and the migration must
    be a no-op there.  A fresh-from-migrations DB (the per-pytest-
    worker template path) lacks the constraints and the migration
    creates them.
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
    """Pre-flight detect violators, then add every missing constraint.

    The pre-flight phase accumulates violators across every spec so a
    single failed run reports the complete cleanup list rather than
    one constraint at a time.  When pre-flight passes, the DDL phase
    adds each constraint that is not already present.  Alembic wraps
    the whole upgrade in a single transaction, so a failure during
    DDL rolls every preceding ADD CONSTRAINT back together with the
    pre-flight reads.

    The ``_constraint_exists`` guard makes the DDL idempotent on
    databases bootstrapped via ``db.create_all()`` (which already
    materialises these constraints from the model declarations).
    """
    bind = op.get_bind()

    # ── Pre-flight ────────────────────────────────────────────────
    violations = []
    for name, _predicate, detection_sql in _CHECK_SPECS:
        rows = bind.execute(sa.text(detection_sql)).fetchall()
        if rows:
            violations.append((name, rows))
    if violations:
        sections = []
        for name, rows in violations:
            rendered = "; ".join(
                "(" + ", ".join(repr(v) for v in row) + ")"
                for row in rows
            )
            sections.append(
                f"{name} -- {len(rows)} offending row(s): {rendered}"
            )
        raise RuntimeError(
            "Refusing to add H-3 CHECK constraints: pre-existing rows "
            "violate one or more predicates.  Resolve every violator by "
            "hand (typically by correcting the offending value or "
            "deleting the row after confirming with the user) and rerun "
            "the migration.  Per docs/coding-standards.md the migration "
            "never auto-rewrites data.  Offenders:\n  "
            + "\n  ".join(sections)
        )

    # ── DDL ───────────────────────────────────────────────────────
    for name, predicate, _detection_sql in _CHECK_SPECS:
        if _constraint_exists(name, "budget"):
            continue
        op.create_check_constraint(
            name, "recurrence_rules", predicate, schema="budget",
        )


def downgrade():
    """Drop every constraint in reverse order.

    Reverse order so a partial failure leaves a recognisable post-
    state (the failed constraint is the last one still present).
    Raw ``ALTER TABLE ... DROP CONSTRAINT IF EXISTS`` is used so the
    downgrade succeeds even on a database that did not carry one of
    the constraints when the upgrade ran (the upgrade's
    _constraint_exists guard would have skipped it).
    """
    for name, _predicate, _detection_sql in reversed(_CHECK_SPECS):
        op.execute(
            f"ALTER TABLE budget.recurrence_rules "
            f"DROP CONSTRAINT IF EXISTS {name}"
        )
