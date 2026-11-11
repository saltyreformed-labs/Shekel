"""a salary profile holds a pay entry: the database refuses one with none

Task **plan#3167** under plan step **salary:X-av-3b** (card plan#1152), from
the card's ``X-ez`` amendment (plan#2025), ruling **R-SAL68**.  MOVES NO
MONEY and alters no table, column or row: it installs
:mod:`app.pay_entry_infrastructure`'s deferred constraint triggers, so that at
COMMIT a salary profile that still exists holds at least one pay entry::

    salary.pay_entries      + ck_profile_keeps_a_pay_entry
                              (AFTER DELETE OR UPDATE OF salary_profile_id)
    salary.salary_profiles  + ck_profile_starts_with_a_pay_entry
                              (AFTER INSERT OR UPDATE OF id)

with the predicate ``salary.assert_profile_holds_a_pay_entry`` and one thin
trigger function per table.  Until now the rule was the doors' alone: the
create door writes a profile's first entry, and Remove refuses the only one.

**It refuses first, writing nothing, while any profile holds no entry**
(:func:`refuse_profiles_without_pay`), naming each.  A constraint trigger
grades writes, not the rows already there, so installing it over such a
profile would leave it standing and refuse the next write that touches it.
There is no repair to make here: what such a profile pays is the owner's fact,
and only the owner can record it.  Migration ``70680a4a7405`` wrote one entry
for every profile and no door has removed the last since, so none is
expected; the refusal is the census, stated rather than assumed.

**The downgrade removes the triggers and their functions** and nothing else,
so it loses no stored fact and restores the schema exactly; no ``Review:``
line is owed.

Revision ID: c3a7e5d91f42
Revises: 5f2c46a35a79
Create Date: 2026-11-10
"""
import sqlalchemy as sa
from alembic import op

from app.pay_entry_infrastructure import (
    apply_pay_entry_infrastructure,
    remove_pay_entry_infrastructure,
)

# revision identifiers, used by Alembic.
revision = "c3a7e5d91f42"
down_revision = "5f2c46a35a79"
branch_labels = None
depends_on = None

#: The census: every profile holding no pay entry.
_PROFILES_WITHOUT_PAY_SQL = """
SELECT p.id, p.user_id, p.name
FROM salary.salary_profiles AS p
WHERE NOT EXISTS (
    SELECT 1 FROM salary.pay_entries AS e WHERE e.salary_profile_id = p.id
)
ORDER BY p.id
"""


def refuse_profiles_without_pay(bind) -> None:
    """Raise when any salary profile holds no pay entry.

    Args:
        bind: The migration's connection.

    Raises:
        RuntimeError: Naming every such profile, with the SELECT that finds
            them and the two ways through: record the profile's pay as its
            first entry, or delete the profile.
    """
    rows = bind.execute(sa.text(_PROFILES_WITHOUT_PAY_SQL)).all()
    if rows:
        named = ", ".join(
            f"id={row.id} user_id={row.user_id} name={row.name!r}"
            for row in rows
        )
        raise RuntimeError(
            f"{len(rows)} salary profile(s) hold no pay entry ({named}), and "
            "this revision makes that state unstorable (ruling R-SAL68).  "
            "Give each its first entry (INSERT INTO salary.pay_entries "
            "(salary_profile_id, payday, amount) with the owner's pay from a "
            "payday they name), or delete the profile, then upgrade again.  "
            f"Inspect: {' '.join(_PROFILES_WITHOUT_PAY_SQL.split())};"
        )


def upgrade():
    """Refuse a profile already holding no entry, then install the backstop."""
    refuse_profiles_without_pay(op.get_bind())
    apply_pay_entry_infrastructure(op.execute)


def downgrade():
    """Remove the backstop's two triggers and three functions."""
    remove_pay_entry_infrastructure(op.execute)
