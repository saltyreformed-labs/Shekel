"""A rule carries its unit's day-of-month flag; the database holds the ceiling and nominal day to it

Plan step plan#2302, ruling plan#2748.  A per-month ceiling means something
only on a paycheck or weekly cadence, and a nominal day only on a monthly or
yearly one, and until this migration only the application refused the wrong
pairing: a restored or hand-edited row could carry either.  The previous
migration (``c4e8a1f7d2b6``) gave the unit's kind its one home,
``ref.recurrence_units.has_day_of_month``.  A CHECK reads only its own row, so
this one carries that flag onto the rule and holds the rule to it:

* ``budget.recurrence_rules.unit_has_day_of_month boolean NOT NULL``, added
  nullable, backfilled from the unit, then made NOT NULL.  NOT NULL is what
  makes the key below hold at all: a composite key with a NULL member is not
  checked (``MATCH SIMPLE``).
* ``fk_recurrence_rules_unit_day_of_month``: ``(unit_id,
  unit_has_day_of_month)`` onto ``uq_recurrence_units_id_has_day_of_month``,
  ``ON DELETE RESTRICT``, beside ``fk_recurrence_rules_unit_id``, which stays
  (existence beside agreement, the ``fk_transactions_owner_account`` shape).
* ``ck_recurrence_rules_max_per_month_unit``: ``max_per_month IS NULL OR NOT
  unit_has_day_of_month``.
* ``ck_recurrence_rules_nominal_day_unit``: ``nominal_day IS NULL OR
  unit_has_day_of_month``.

**The census runs first, and it clears rather than refuses.**  Each rule
carrying a ceiling on a month or year unit, or a nominal day on a period or
week unit, has that value set to NULL and is printed, id and value, because
clearing moves no occurrence: a month or year walk yields one date per month
at most, so ``_occurrence._ceilinged`` never drops one and no ceiling of 1 or
more binds (the column's own CHECK is its floor), and the read door already
drops a nominal day the unit cannot hold (``_reading.recurrence_spec_with_cadence``).
Measured on the production dump of 2026-11-03, carried to this revision by
``scripts/init_database.py``: 57 rules (16 month, 19 period, 22 year), 0 of
each; the 12 period rules carrying a ceiling are legal and stay.  The backfill
added one audit row per rule, 57.

The downgrade drops the four objects and restores no cleared value.  No
occurrence or figure read one.  The edit form did, prefilling a stored ceiling
or nominal day it could carry onto a later change of unit, which is one more
reason the value goes.  ``system.audit_log`` holds what the UPDATE replaced for
its retention window (``AUDIT_RETENTION_DAYS``, 365 days by default).

Self-contained dependency policy: this migration imports nothing from ``app``.

Revision ID: 9b3e6d1f4a27
Revises: c4e8a1f7d2b6
Create Date: 2026-11-04
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "9b3e6d1f4a27"
down_revision = "c4e8a1f7d2b6"
branch_labels = None
depends_on = None

_TABLE = "recurrence_rules"
_COLUMN = "unit_has_day_of_month"
_KEY = "fk_recurrence_rules_unit_day_of_month"
_CEILING_CHECK = "ck_recurrence_rules_max_per_month_unit"
_NOMINAL_DAY_CHECK = "ck_recurrence_rules_nominal_day_unit"

#: Each column the census clears, with the predicate over its unit that names
#: a value the unit cannot hold.
_UNHOLDABLE = {
    "max_per_month": "u.has_day_of_month",
    "nominal_day": "NOT u.has_day_of_month",
}

_BACKFILL = sa.text(
    "UPDATE budget.recurrence_rules r "
    "SET unit_has_day_of_month = u.has_day_of_month "
    "FROM ref.recurrence_units u WHERE u.id = r.unit_id"
)
_UNFILLED = sa.text(
    "SELECT count(*) FROM budget.recurrence_rules "
    "WHERE unit_has_day_of_month IS NULL"
)


def _clear_what_the_unit_cannot_hold(bind) -> None:
    """Set each value its rule's unit cannot hold to NULL, printing each one.

    Args:
        bind: The migration's connection.
    """
    for column, unholdable in _UNHOLDABLE.items():
        rows = bind.execute(sa.text(
            f"SELECT r.id, r.{column} AS value, u.name AS unit "
            f"FROM budget.recurrence_rules r "
            f"JOIN ref.recurrence_units u ON u.id = r.unit_id "
            f"WHERE r.{column} IS NOT NULL AND {unholdable} ORDER BY r.id"
        )).all()
        if rows:
            bind.execute(
                sa.text(
                    f"UPDATE budget.recurrence_rules SET {column} = NULL "
                    f"WHERE id = ANY(:ids)"
                ),
                {"ids": [row.id for row in rows]},
            )
        cleared = "".join(
            f" rule {row.id} ({row.unit}, {column} {row.value});" for row in rows
        )
        print(f"plan#2302: cleared {len(rows)} {column} value(s).{cleared}")


def upgrade():
    """Clear what no unit can hold, carry the flag, then hold the rule to it."""
    bind = op.get_bind()
    _clear_what_the_unit_cannot_hold(bind)

    op.add_column(
        _TABLE, sa.Column(_COLUMN, sa.Boolean(), nullable=True), schema="budget",
    )
    bind.execute(_BACKFILL)
    unfilled = bind.execute(_UNFILLED).scalar_one()
    if unfilled:
        raise RuntimeError(
            f"budget.recurrence_rules: {unfilled} row(s) took no "
            f"unit_has_day_of_month from the backfill, so the column cannot "
            f"be made NOT NULL.  Diagnose with: SELECT r.id, r.unit_id FROM "
            f"budget.recurrence_rules r WHERE r.unit_has_day_of_month IS NULL;"
        )
    op.alter_column(_TABLE, _COLUMN, nullable=False, schema="budget")

    op.create_foreign_key(
        _KEY, _TABLE, "recurrence_units",
        ["unit_id", _COLUMN], ["id", "has_day_of_month"],
        source_schema="budget", referent_schema="ref", ondelete="RESTRICT",
    )
    op.create_check_constraint(
        _CEILING_CHECK, _TABLE,
        "max_per_month IS NULL OR NOT unit_has_day_of_month",
        schema="budget",
    )
    op.create_check_constraint(
        _NOMINAL_DAY_CHECK, _TABLE,
        "nominal_day IS NULL OR unit_has_day_of_month",
        schema="budget",
    )


def downgrade():
    """Drop the two CHECKs, the key and the flag; a cleared value stays cleared."""
    op.drop_constraint(_NOMINAL_DAY_CHECK, _TABLE, schema="budget", type_="check")
    op.drop_constraint(_CEILING_CHECK, _TABLE, schema="budget", type_="check")
    op.drop_constraint(_KEY, _TABLE, schema="budget", type_="foreignkey")
    op.drop_column(_TABLE, _COLUMN, schema="budget")
