"""a repeat on each statement's due date

Revision ID: e2b8c4f1a9d3
Revises: d1f4a7c2b8e5
Create Date: 2026-11-03

Plan step **credit_card:CC-6**, its task plan#2673 (ruling plan#2671: "a
card payment's dates are its repeat; a new repeat is each statement's due
date")::

    ref.recurrence_units    + ('due_date')

**One reference row and nothing else.**  A rule repeating by it fires on each
statement's due date, worked out from the card's dated terms on every read
(:mod:`app.services.card_statement`), so no column names a day and no rule
row changes here.  ``budget.recurrence_rules.unit_id`` already references
this table under ``RESTRICT``, so nothing on the rule table moves.  The row
states no ``months_per_unit`` (migration ``c4e8a1f7d2b6``): the unit is not
measured in months, so its generated ``has_day_of_month`` is false, and the
rule table's CHECKs over the flag a rule carries (``9b3e6d1f4a27``) refuse a
nominal day beside it (none, plan#2673) and a weekday reading (``897a6663d221``).

**Inline seed rationale.**  The row is seeded here so ``ref_cache.init()``
resolves :class:`~app.enums.RecurrenceUnitEnum`'s new member immediately
after a bare ``flask db upgrade``; ``app/ref_seeds.py`` carries the identical
row for the idempotent reseed (the dual-seed pattern
``tests/test_models/test_posting_ref_seed_parity.py`` guards).
``ON CONFLICT (name) DO NOTHING`` makes the insert idempotent against a
partial re-run and against the entrypoint's later reseed.

**Reversible, refusing while a rule repeats by it.**  The downgrade deletes
the row; a rule naming it would hold the delete back under the ``RESTRICT``
key with an error naming neither the rule nor what to do, so the downgrade
counts them first and says.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = 'e2b8c4f1a9d3'
down_revision = 'd1f4a7c2b8e5'
branch_labels = None
depends_on = None


# Inline seed SQL.  The name MUST match ``RecurrenceUnitEnum.DUE_DATE.value``
# and ``app/ref_seeds.py``'s entry exactly, or ``ref_cache.init()`` raises at
# app start; it is a literal inside the INSERT because that is where the seed
# parity test (``tests/test_models/test_posting_ref_seed_parity.py``) reads it.
_SEED_SQL = (
    "INSERT INTO ref.recurrence_units (name) VALUES ('due_date') "
    "ON CONFLICT (name) DO NOTHING"
)

#: The rules that repeat by the unit, which the downgrade cannot keep.
_RULES_REPEATING_BY_IT = (
    "SELECT r.id FROM budget.recurrence_rules r "
    "JOIN ref.recurrence_units u ON u.id = r.unit_id "
    "WHERE u.name = 'due_date' ORDER BY r.id"
)

_UNSEED_SQL = "DELETE FROM ref.recurrence_units WHERE name = 'due_date'"


def upgrade():
    """Seed the due-date unit."""
    op.execute(_SEED_SQL)


def downgrade():
    """Delete the due-date unit, refusing while any rule repeats by it."""
    rules = op.get_bind().execute(
        sa.text(_RULES_REPEATING_BY_IT),
    ).scalars().all()
    if rules:
        named = ", ".join(str(rule_id) for rule_id in rules)
        raise RuntimeError(
            f"Cannot remove the due-date repeat: recurrence rules {named} "
            f"repeat by it, and the older schema has no unit to carry them. "
            f"List them with: {_RULES_REPEATING_BY_IT}; re-author each on "
            f"another repeat or delete its definition, then run the downgrade "
            f"again."
        )
    op.execute(_UNSEED_SQL)
