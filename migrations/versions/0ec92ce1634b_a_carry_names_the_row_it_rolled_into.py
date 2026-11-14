"""a carry names the row it rolled into

Task **plan#3985** under plan step **balance:X-ax** (card plan#1103), ruling
**plan#3973**: *a carry remembers its source envelope; the carried amount is
derived each time, never stored.*  MOVES NO MONEY in this revision: it adds the
link and the rules that hold it, and nothing in ``app/`` writes or reads the
column yet.  Every figure the app renders is byte-identical the moment this
lands, because every row's link is NULL.

``budget.transactions.rolled_into_id``, nullable, on the SOURCE row: the row a
carry-forward rolled this envelope's unspent budget into.  One column, so a
source rolls into at most one row; a row may receive several (a non-unique
index).  NULL is the fact "nothing of this row's was carried", which is every
row today.  The link is the only thing a carry stores: the amount it carries is
derived from the source each time it is read (plan#3987), so there is nothing
to keep in step (rule 14).

What this revision installs, each a backstop behind a door that refuses first
(coding rules: a database refusal is never the check):

  1. ``uq_transactions_id_user_template_scenario`` -- the SUPERKEY the link's
     composite key targets.  It constrains nothing (``id`` is the primary key)
     and exists because PostgreSQL requires a UNIQUE over exactly the
     referenced columns, the construction ``uq_transactions_id_user`` already
     uses for the movements' owner key.
  2. ``fk_transactions_rolled_into`` -- ``(rolled_into_id, user_id,
     template_id, scenario_id)`` onto that superkey, so a carry lands only in a
     row of the SAME owner, the SAME definition and the SAME scenario.  The
     owner is named because a row's ``template_id`` is a plain key: a shared
     definition does not imply a shared owner in the schema.
     ``ON DELETE RESTRICT``: a row holding carried money cannot vanish from
     under its source; the door that deletes one is to release its incoming
     links first (plan#3993), and this refuses a writer that does not.
  3. ``ck_transactions_carry_names_a_definition`` -- a linked row names a
     definition.  It is what makes (2) bind at all: PostgreSQL's default
     ``MATCH SIMPLE`` skips a composite key whenever ANY of its columns is
     NULL, ``template_id`` is the one nullable column among the four, and a
     row with no definition (a CC payback) would otherwise carry a link the
     key never checks -- into another owner's row, or into no row.  ``MATCH
     FULL`` cannot stand in for it: it would refuse every unlinked row of a
     definition, which is nearly every row.
  4. ``ck_transactions_carry_needs_settle_day`` -- a linked row has a settle
     day, twin of ``ck_transactions_cleared_needs_settle_day``.  A carry is the
     settle's own act: the source settles at what it spent and rolls the rest
     forward, so the link has the settle assertion's lifetime, and the
     revert that releases the day is to release the link in the same write
     (plan#3991).
  5. ``ck_transactions_carry_not_into_itself`` -- ``rolled_into_id <> id``.
     The composite key admits a self-link, since a row agrees with itself on
     owner, definition and scenario, and a row carried into itself would count
     its own unspent budget twice.
  6. ``idx_transactions_rolled_into`` -- partial, over the linked rows only.
     "Which sources rolled into this row" is the derivation's read, and the
     key's own delete check runs it too; a foreign key indexes only the
     REFERENCED side.

**Audit.**  ``budget.transactions`` is already in
``app.audit_infrastructure.AUDITED_TABLES``; its row trigger captures the new
column with no change here.

**The downgrade REFUSES while any row holds a link**
(:func:`refuse_recorded_carries`), naming the count, and otherwise removes
exactly what the upgrade added.  Once the carry-forward writes links
(plan#3989), a link is the only record that its target holds carried money, and
the code before this revision cannot derive it: dropping the column would take
every carry's money out of its target's projection without a word.  No SQL can
fold it back by hand, because the carried amount is the resolver's derivation
from the source's definition, so the refusal names the rows and leaves the fold
to whoever downgrades.  At this revision nothing writes the column, so the
downgrade succeeds wherever this revision is the newest code that has run.  The
revision that rebuilds the existing carries from the change log (plan#3995)
sits above this one and downgrades first: what it does with the links it
wrote decides whether a downgrade through both can run.

Revision ID: 0ec92ce1634b
Revises: c3a7e5d91f42
Create Date: 2026-11-13
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0ec92ce1634b"
# The head of ``dev`` at ``2dde73df2``, measured on ``card/1103`` at its claim.
# Another lane landing a revision first makes two heads, which git merges
# cleanly and ``flask db upgrade`` refuses: re-point this at the measured head
# as the last edit before the pull request.
down_revision = "c3a7e5d91f42"
branch_labels = None
depends_on = None

#: The census the downgrade refuses on: every row that names the row its carry
#: rolled into.
_RECORDED_CARRIES_SQL = (
    "SELECT count(*) FROM budget.transactions WHERE rolled_into_id IS NOT NULL"
)


def refuse_recorded_carries(connection) -> None:
    """Refuse the downgrade while any row records the row its carry rolled into.

    Public because ``tests/test_models/test_the_carry_link_schema.py`` drives
    it by name, as ``refuse_profiles_without_pay`` is driven in its revision.

    Args:
        connection: The migration's bound connection.

    Raises:
        RuntimeError: One or more rows hold a link, with the count and the
            SELECT that lists them.  Nothing has been dropped when it raises.
    """
    recorded = connection.execute(sa.text(_RECORDED_CARRIES_SQL)).scalar()
    if recorded:
        raise RuntimeError(
            f"{recorded} budget.transactions row(s) record the row their "
            "carry-forward rolled into (rolled_into_id).  Dropping the column "
            "takes each carry's money out of its target's projection, and the "
            "code before revision 0ec92ce1634b cannot derive it.  Fold each "
            "carry into its target's own amount and clear the links first.  "
            "List them with: SELECT id, rolled_into_id FROM "
            "budget.transactions WHERE rolled_into_id IS NOT NULL;"
        )


def upgrade():
    """Add the carry link, its superkey, its key, its three CHECKs and its index.

    The superkey exists before the foreign key that targets it.  The column is
    added NULL on every row, which every constraint below admits, so nothing is
    backfilled and nothing can refuse the upgrade.
    """
    op.create_unique_constraint(
        "uq_transactions_id_user_template_scenario",
        "transactions", ["id", "user_id", "template_id", "scenario_id"],
        schema="budget",
    )
    op.add_column(
        "transactions",
        sa.Column("rolled_into_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.create_foreign_key(
        "fk_transactions_rolled_into",
        "transactions", "transactions",
        ["rolled_into_id", "user_id", "template_id", "scenario_id"],
        ["id", "user_id", "template_id", "scenario_id"],
        source_schema="budget", referent_schema="budget",
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_transactions_carry_names_a_definition",
        "transactions",
        "rolled_into_id IS NULL OR template_id IS NOT NULL",
        schema="budget",
    )
    op.create_check_constraint(
        "ck_transactions_carry_needs_settle_day",
        "transactions",
        "rolled_into_id IS NULL OR settled_on IS NOT NULL",
        schema="budget",
    )
    op.create_check_constraint(
        "ck_transactions_carry_not_into_itself",
        "transactions",
        "rolled_into_id <> id",
        schema="budget",
    )
    op.create_index(
        "idx_transactions_rolled_into", "transactions", ["rolled_into_id"],
        unique=False, schema="budget",
        postgresql_where=sa.text("rolled_into_id IS NOT NULL"),
    )


def downgrade():
    """Refuse while a carry is recorded; otherwise drop what upgrade added.

    Reverse dependency order: the index, the CHECKs and the key go before the
    column they read, and the superkey after the key that targets it.
    """
    refuse_recorded_carries(op.get_bind())
    op.drop_index(
        "idx_transactions_rolled_into", table_name="transactions",
        schema="budget",
    )
    for name in (
        "ck_transactions_carry_not_into_itself",
        "ck_transactions_carry_needs_settle_day",
        "ck_transactions_carry_names_a_definition",
    ):
        op.drop_constraint(name, "transactions", type_="check", schema="budget")
    op.drop_constraint(
        "fk_transactions_rolled_into", "transactions",
        type_="foreignkey", schema="budget",
    )
    op.drop_column("transactions", "rolled_into_id", schema="budget")
    op.drop_constraint(
        "uq_transactions_id_user_template_scenario", "transactions",
        type_="unique", schema="budget",
    )
