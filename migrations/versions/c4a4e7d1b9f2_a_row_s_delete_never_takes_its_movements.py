"""A row's delete never takes its movements, and a match's movement key stops cascading

Revision ID: c4a4e7d1b9f2
Revises: 70680a4a7405
Create Date: 2026-09-22 22:40:00.000000
Review: developer, 2026-09-22 / 2026-09-23 (rulings R-CC54 parts (2) and (3),
R-CC63..R-CC66, R-CC82: three foreign keys dropped and re-created NO ACTION,
and the two refusals before them; R-CC89 and R-CC92: the deleted-row
triggers)

Plan step ``credit_card:CC-5-4a-4``.  Rulings **R-CC54** parts (2) and (3)
(developer 2026-09-22): *"The database stops cascading a row's delete to its
payments and purchases.  A row holding one is history, so the template and
account permanent deletes archive instead, and truncate/regenerate lock its
period ... A match's key to its payment or purchase stops cascading, like its
key to the bank line.  The screens' leftover-match check is deleted."*  With
**R-CC63**..**R-CC66**, which carry the rule to the archives, the
object layer, the reset and the transfer doors.  Closes ledger row
**CC-363**.

**What was wrong.**  ``budget.transaction_entries`` keyed its parent row with
``ON DELETE CASCADE`` twice (the single-column ``transaction_id`` key and the
composite ``fk_transaction_entries_owner_transaction``), so every statement
that deleted a row deleted the payments and purchases under it -- and three
doors did that as a SIDE EFFECT of deleting something else: the template and
account permanent deletes in bulk SQL, and the pay-period truncate /
regenerate / reset through ``transactions.pay_period_id``'s own cascade.
Measured 2026-09-22 on a copy of production: permanently deleting one
definition destroyed the recorded purchase under its current row and moved
its account's projection for that period.  And a match's
key to its movement cascaded too, so a destroyed movement left its act naming
a bank line alone -- a false "explained" a predicate in the one read that
decides (``_candidates.act_still_names_a_row``) stopped counting rather than
anything preventing.

**What this revision does.**  All three keys become ``NO ACTION``: a row
holding a movement cannot be deleted, and a movement a match names cannot be
deleted, by any statement.  The single-column key is re-created under a name
(``fk_transaction_entries_transaction_id``; it carried Postgres' default until
now).  No row moves and no value changes -- the application's doors, in the
same release, keep a row holding a movement instead of deleting it, and the
one act that takes a movement off the books removes it from its matches and
then deletes it before its row.

**Why it refuses on a stranded act.**  The leftover-match check is deleted in
the same release because this revision makes its subject unrepresentable --
but only going FORWARD.  An act that ALREADY names no movement (its last one
destroyed by a cascade before today) would, with the check gone, read as
explaining its bank lines forever.  Measured 0 of 284 on the 17:06 dump; a
non-zero count is the developer's to rule, so the revision names the acts and
writes nothing.

**Why it refuses on a hidden row holding a movement** (ruling **R-CC82**,
developer 2026-09-23: *"The release's database update counts such rows and
refuses to run if any exist, naming them, the same way it already refuses on
orphaned statement matches."*).  The same release makes a deleted recurring
occurrence give up its payments and purchases (ruling **R-CC75**) and an
archive hide only rows holding nothing (**R-CC63**) -- going FORWARD.  A row
the code before it hid while it still held one would lock its pay period
(truncate, regenerate and reset refuse over it) with no screen able to reach
the row.  Measured 0 on production at 2026-09-23 12:08 UTC.  **A transfer's
shadow is excepted** (the developer's follow-up the same morning, "Non-transfer
rows"): the transfer's own soft delete still hides a leg holding its kept
payment after this release, which is finding **BAL-532**'s
(``balance:X-bi-6-4``) to end, and a refusal over a state the release goes on
creating would stop nothing it fixes.

**And a deleted row holds no money from here on** (ruling **R-CC89**,
developer 2026-09-23: *"The database refuses any payment or purchase written
under a deleted row, so no door, now or later, can do it."*, and **R-CC92**
extending it the same day, "Refuse both ways": hiding a row that still holds
one is refused too, a transfer's leg excepted as BAL-532's until X-bi-6-4).  The revision installs
:mod:`app.deleted_row_infrastructure`'s two triggers after the keys: a
movement arriving under a deleted row is refused, and so is a commit that
leaves a non-transfer row hidden while it holds one.  Each grades a write,
never a stored row, so neither refuses anything already there -- and the
refusal above has already stopped the upgrade over the one stored state the
second would forbid.  The downgrade removes them FIRST, before the keys
cascade again, and they depend on nothing the key flips do.

``tests/test_models/test_cc5_4a4_row_keeps_its_movements.py`` drives the
shipped ``upgrade`` / ``downgrade``: each key's refusal after the upgrade and
its cascade after the downgrade, both refusals and their controls, the
triggers' install and removal, and the round trip.
"""
from alembic import op
import sqlalchemy as sa

from app.deleted_row_infrastructure import (
    ROW_ARM,
    apply_deleted_row_infrastructure,
    remove_deleted_row_infrastructure,
)


# revision identifiers, used by Alembic.
revision = "c4a4e7d1b9f2"
down_revision = "70680a4a7405"
branch_labels = None
depends_on = None


#: Every act naming no movement -- a match left holding bank lines alone.  The
#: shape ``_candidates.act_still_names_a_row`` filtered at read time, asked of
#: the stored past once, here, before the filter is deleted.
_STRANDED_ACTS_SQL = (
    "SELECT a.id FROM budget.statement_matches a "
    " WHERE NOT EXISTS (SELECT 1 FROM budget.statement_match_members m "
    "                    WHERE m.match_id = a.id "
    "                      AND m.transaction_entry_id IS NOT NULL) "
    " ORDER BY a.id"
)

#: Every hidden row that is not a transfer's shadow and still holds a payment
#: or purchase -- a row whose period would lock with no screen able to reach
#: it (ruling **R-CC82**).
_HIDDEN_HOLDING_ROWS_SQL = (
    "SELECT t.id FROM budget.transactions t "
    " WHERE t.is_deleted "
    "   AND t.transfer_id IS NULL "
    "   AND EXISTS (SELECT 1 FROM budget.transaction_entries e "
    "                WHERE e.transaction_id = t.id) "
    " ORDER BY t.id"
)

#: The three keys this revision flips, as ``(name, table, columns, referent,
#: referent columns)``.  The single-column key's OLD name is Postgres'
#: default, which the downgrade restores.
_ROW_KEY_OLD_NAME = "transaction_entries_transaction_id_fkey"
_ROW_KEY_NEW_NAME = "fk_transaction_entries_transaction_id"
_OWNER_KEY = (
    "fk_transaction_entries_owner_transaction", "transaction_entries",
    ["transaction_id", "owner_id"], "transactions", ["id", "user_id"],
)
_MEMBER_KEY = (
    "fk_statement_match_members_entry_account", "statement_match_members",
    ["transaction_entry_id", "account_id"], "transaction_entries",
    ["id", "account_id"],
)


def refuse_stranded_acts(bind) -> None:
    """Refuse the upgrade while any act names no movement.

    **Module-level so a test can DRIVE the refusal** (the chain's own pattern,
    ``2eabfa596ee0.refuse_unkeyable_row_members``).

    Args:
        bind: A SQLAlchemy connection.

    Raises:
        RuntimeError: Naming each such act's ``statement_matches`` id and the
            diagnostic query.  Nothing has been written.
    """
    stranded = [row[0] for row in bind.execute(sa.text(_STRANDED_ACTS_SQL))]
    if stranded:
        raise RuntimeError(
            f"CC-5-4a-4 refuses: {len(stranded)} statement match(es) name no "
            f"movement (ids {stranded}; diagnose with: {_STRANDED_ACTS_SQL}).  "
            "With the leftover-match check deleted each would read as "
            "explaining its bank lines forever.  Nothing was written; each is "
            "the developer's to rule (ruling R-CC54)."
        )


def refuse_hidden_rows_holding_movements(bind) -> None:
    """Refuse the upgrade while any hidden non-transfer row holds a movement.

    Module-level so a test can DRIVE the refusal, as
    :func:`refuse_stranded_acts`.

    Args:
        bind: A SQLAlchemy connection.

    Raises:
        RuntimeError: Naming each such row's ``transactions`` id and the
            diagnostic query.  Nothing has been written.
    """
    hidden = [row[0] for row in bind.execute(sa.text(_HIDDEN_HOLDING_ROWS_SQL))]
    if hidden:
        raise RuntimeError(
            f"CC-5-4a-4 refuses: {len(hidden)} hidden row(s) hold a recorded "
            f"payment or purchase (ids {hidden}; diagnose with: "
            f"{_HIDDEN_HOLDING_ROWS_SQL}).  Each would lock its pay period "
            "with no screen able to reach it.  Nothing was written; each is "
            "the developer's to rule (ruling R-CC82)."
        )


def _recreate(key, *, ondelete, name=None) -> None:
    """Drop *key* and create it again with *ondelete*, optionally renamed.

    Args:
        key: One of the ``(name, table, columns, referent, referent columns)``
            tuples above.
        ondelete: The new ``ON DELETE`` action, ``None`` for NO ACTION.
        name: The name to create it under, when it is not the old one.
    """
    old_name, table, columns, referent, referent_columns = key
    op.drop_constraint(old_name, table, schema="budget", type_="foreignkey")
    op.create_foreign_key(
        name or old_name, table, referent, columns, referent_columns,
        source_schema="budget", referent_schema="budget", ondelete=ondelete,
    )


def upgrade():
    """Make a row's movements and a match's movements undeletable by cascade."""
    bind = op.get_bind()
    refuse_stranded_acts(bind)
    refuse_hidden_rows_holding_movements(bind)
    row_key = (
        _ROW_KEY_OLD_NAME, "transaction_entries", ["transaction_id"],
        "transactions", ["id"],
    )
    _recreate(row_key, ondelete=None, name=_ROW_KEY_NEW_NAME)
    _recreate(_OWNER_KEY, ondelete=None)
    _recreate(_MEMBER_KEY, ondelete=None)
    # The arm this revision declared, as a LITERAL: the module grows arms that
    # name columns later revisions add (plan step balance:X-bi-6-4d-2).
    apply_deleted_row_infrastructure(op.execute, arms=(ROW_ARM,))
    print(
        "CC-5-4a-4: transaction_entries' two row keys and "
        "statement_match_members' movement key are NO ACTION; 0 stranded "
        "acts; 0 hidden rows holding a movement; a movement arriving under a "
        "deleted row, and a row hidden while it holds one, are refused."
    )


def downgrade():
    """Remove the deleted-row triggers, then restore the three keys' cascade."""
    remove_deleted_row_infrastructure(op.execute)
    row_key = (
        _ROW_KEY_NEW_NAME, "transaction_entries", ["transaction_id"],
        "transactions", ["id"],
    )
    _recreate(row_key, ondelete="CASCADE", name=_ROW_KEY_OLD_NAME)
    _recreate(_OWNER_KEY, ondelete="CASCADE")
    _recreate(_MEMBER_KEY, ondelete="CASCADE")
