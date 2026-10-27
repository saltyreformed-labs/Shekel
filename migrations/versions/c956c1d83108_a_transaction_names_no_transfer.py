"""a transaction names no transfer: its transfer column and the parent-transfer source go

Revision ID: c956c1d83108
Revises: b7d2e4f19a63
Create Date: 2026-10-27
Review: developer, 2026-09-20 (ruling R-BAL86: X-bi-6-5 deletes the rows and
transactions.transfer_id with its constraints by migration) and 2026-10-27
(ruling plan#1843: X-bi-6-5 is four leaf steps, the first drops the
transaction's transfer column, every branch that tests it and the unused
parent-transfer amount source) and 2026-10-27 (ruling plan#1851: leaf 1 drops
82f7b7ed4332's twin copy; destructive: a column, its foreign key, two indexes
and a CHECK dropped, a CHECK re-cut without the column's term, a reference row
deleted, and a table of kept rows dropped)

Plan step **balance:X-bi-6-5**, its first leaf, card plan#1844.  A transfer
had two hidden twin ``budget.transactions`` rows, each naming it by
``transfer_id`` and declaring its amount ``parent_transfer`` (worth what the
transfer is: amount rule 5).  Revision ``82f7b7ed4332`` (plan step
``balance:X-bi-6-4d-2``) deleted every twin and added
``ck_transactions_names_no_transfer`` (``transfer_id IS NULL``), so no stored
row names a transfer and the column is empty on every row.  This revision
deletes the column and what existed only for it:

  1. **It refuses, writing nothing, while a stored row still declares its
     amount ``parent_transfer``** (:func:`refuse_rows_declaring_a_parent_transfer`),
     naming each table holding one with its count, its first ids and the query
     that finds them.  The tables are read from the CATALOG, every foreign key
     into ``ref.amount_sources`` (``budget.transactions`` and
     ``budget.transfers`` at this revision), so a key the schema gains is
     counted without a second list to keep in step.  Such a row is storable
     (a row of a definition may declare it under every CHECK), no door writes
     one since the twins went, and its own key (``RESTRICT``) would refuse
     the reference row's delete with a raw error: this refusal says what to
     repair first.
  2. It re-cuts ``ck_transactions_one_pricing_link`` to two terms,
     ``template_id`` and ``credit_payback_for_id``, still ``= 1``.  **The
     re-cut is explicit because PostgreSQL's ``DROP COLUMN`` silently drops
     every CHECK that names the column**, a multi-column one included, so
     leaving it to the drop would delete the whole rule that a row is priced
     through exactly one relation.  Every stored row passes the two-term form:
     the three-term form held ``= 1`` and ``ck_transactions_names_no_transfer``
     held the third term at zero.
  3. It drops ``ck_transactions_names_no_transfer``,
     ``uq_transactions_transfer_type_active`` and ``idx_transactions_transfer``
     by name, so a database missing one is refused rather than passed.
  4. It drops ``transactions.transfer_id``, and the drop takes the column's
     foreign key: a migrated database names it ``fk_transactions_transfer_id``
     (``772043eee094``) and a ``create_all`` one leaves it unnamed, so it is
     not dropped by either name.
  5. It deletes the ``parent_transfer`` row of ``ref.amount_sources``, which
     names a relation no row can now hold.  ``template`` is the one relation
     left; the OWN state is the absence of one.
  6. **It drops the twin copy**, ``system.transfer_twin_purge``
     (ruling **plan#1851**, "Drop it in leaf 1"): ``82f7b7ed4332``'s kept copy
     of every twin it deleted, read by that revision's downgrade alone, which
     restored each twin EXACTLY from it (ruling **R-BAL260**).  Only where it
     exists: a database built at head by ``scripts/init_database.py``
     (``create_all``, then a stamp) never ran that upgrade and holds none.

**No money moves.**  No reader reads the column: plan#1844's first commit
(``bec6f2218``) deleted every branch that tested it, and this revision's own
commit deletes amount rule 5, whose refusal of a row declaring the relation
was the last code to read it.  No row declares that relation (step 1).  A database at head holds
no function, trigger, view or policy reading ``transactions.transfer_id``
(``pg_proc``, ``pg_trigger``, ``pg_views``, ``pg_policy``; censused on the
test image at ``b7d2e4f19a63``, 2026-10-27).  The deleted-row rule's row-only
text still names it (:mod:`app.deleted_row_infrastructure`), and only
``c4a4e7d1b9f2``'s upgrade and ``e616adf7fe22``'s downgrade install that text,
each leaving a revision where the column stands.

**What the copy's drop costs a rollback, stated because it is the only thing
here that cannot be undone.**  Without the copy, ``82f7b7ed4332``'s downgrade
reads a missing copy as an empty one (ruling **R-BAL261**) and BUILDS every
twin from its transfer: new ids, the transfer's category, a derived figure and
no ``$0.00``-close day, where the copy restored each twin's own id, times,
version counter, category, figure and day.  ``c4e91a7b2d38``'s downgrade,
which writes old due dates back by the twin ids its backfill table names,
then updates no twin.  No reader at or above ``82f7b7ed4332`` reads a twin,
so no figure any screen shows depends on the copy.

**The downgrade restores each piece but the copy**: the ``parent_transfer``
reference row
(by name, a new id where the old one was lost: every revision below resolves
it by name), the nullable column with its key named
``fk_transactions_transfer_id`` and ``ON DELETE CASCADE`` (the name
``772043eee094``'s downgrade drops), the two indexes, the three-term CHECK and
``ck_transactions_names_no_transfer``.  The column comes back EMPTY and at the
end of the table rather than where it stood; every revision below reads it by
name, and ``82f7b7ed4332``'s downgrade, which runs next, rebuilds the twins
into it from rows typed ``budget.transactions`` as the table then stands.  The
copy is not re-created: its rows went with it, and that downgrade creates the
table it reads when it finds none.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c956c1d83108"
down_revision = "b7d2e4f19a63"
branch_labels = None
depends_on = None


#: The relation this revision deletes, by name: a migration resolves a ref row
#: by name because its id differs between databases.
_PARENT_TRANSFER = "parent_transfer"

#: How many offending ids a refusal names per table; the query finds the rest.
_NAMED_AT_MOST = 20

#: Every foreign key into ``ref.amount_sources.id``, read from the CATALOG:
#: ``(referencing table, its column)``, each quoted for SQL.  DISTINCT because
#: one column could carry two keys, and one reference is one reference.
_FOREIGN_KEYS_INTO_AMOUNT_SOURCES_SQL = """
SELECT DISTINCT quote_ident(n.nspname) || '.' || quote_ident(cl.relname),
       quote_ident(a.attname)
  FROM pg_constraint c
  JOIN pg_class cl ON cl.oid = c.conrelid
  JOIN pg_namespace n ON n.oid = cl.relnamespace
  CROSS JOIN LATERAL unnest(c.conkey, c.confkey) AS k(local, referenced)
  JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.local
  JOIN pg_attribute r ON r.attrelid = c.confrelid AND r.attnum = k.referenced
 WHERE c.contype = 'f'
   AND c.confrelid = 'ref.amount_sources'::regclass
   AND r.attname = 'id'
 ORDER BY 1, 2
"""

#: The rows of one referencing table declaring the relation, ``{table}`` and
#: ``{column}`` filled from :data:`_FOREIGN_KEYS_INTO_AMOUNT_SOURCES_SQL`.
_DECLARING_SQL = (
    "SELECT id FROM {table} WHERE {column} = "
    "(SELECT id FROM ref.amount_sources WHERE name = :name) ORDER BY id"
)

#: The CHECK this revision re-cuts, and its condition on each side of it.
_ONE_LINK = "ck_transactions_one_pricing_link"
_ONE_LINK_WITHOUT_TRANSFER = (
    "(template_id IS NOT NULL)::int "
    "+ (credit_payback_for_id IS NOT NULL)::int = 1"
)
_ONE_LINK_WITH_TRANSFER = (
    "(template_id IS NOT NULL)::int "
    "+ (transfer_id IS NOT NULL)::int "
    "+ (credit_payback_for_id IS NOT NULL)::int = 1"
)

#: ``82f7b7ed4332``'s refusal of a row naming a transfer, and its condition.
_NAMES_NO_TRANSFER = "ck_transactions_names_no_transfer"
_NAMES_NO_TRANSFER_CONDITION = "transfer_id IS NULL"

#: ``82f7b7ed4332``'s copy of every twin it deleted, which this revision drops.
_TWIN_COPY = "system.transfer_twin_purge"

#: Whether the copy exists: a database built at head by ``create_all`` has none.
_TWIN_COPY_PRESENT_SQL = f"SELECT to_regclass('{_TWIN_COPY}') IS NOT NULL"

#: The two indexes over the column, ``(name, columns, unique, predicate)``, as
#: ``772043eee094`` and ``c21a1f0b8e74`` created them.
_INDEXES = (
    (
        "idx_transactions_transfer", ["transfer_id"], False,
        "transfer_id IS NOT NULL",
    ),
    (
        "uq_transactions_transfer_type_active",
        ["transfer_id", "transaction_type_id"], True,
        "transfer_id IS NOT NULL AND is_deleted = FALSE",
    ),
)


def refuse_rows_declaring_a_parent_transfer(bind) -> None:
    """Refuse the upgrade while a stored row declares its amount ``parent_transfer``.

    Asked before any write, so a refusal keeps nothing.  Module-level so a
    test can DRIVE it, as ``b7d2e4f19a63``'s census function is.

    Args:
        bind: A SQLAlchemy connection.

    Raises:
        RuntimeError: Naming each table holding such a row with its count, its
            first ids and the query that finds them.
    """
    found = []
    for table, column in bind.execute(
        sa.text(_FOREIGN_KEYS_INTO_AMOUNT_SOURCES_SQL),
    ).all():
        query = _DECLARING_SQL.format(table=table, column=column)
        ids = bind.execute(
            sa.text(query), {"name": _PARENT_TRANSFER},
        ).scalars().all()
        if ids:
            found.append((table, len(ids), ids[:_NAMED_AT_MOST], query))
    if found:
        raise RuntimeError(
            "X-bi-6-5 refuses: row(s) still declare their amount comes from "
            "a parent transfer, a relation this revision deletes, by (table, "
            "count, first ids): "
            f"{[(table, count, ids) for table, count, ids, _query in found]}.  "
            "Diagnose each with: "
            f"{[query for _table, _count, _ids, query in found]}, binding "
            f":name to {_PARENT_TRANSFER!r}.  No door writes one since "
            "X-bi-6-4d-2 deleted the transfer twins: a row of a definition "
            "re-declares 'template' or states its own figure, and which is "
            "the developer's to rule.  Nothing was written: the revision "
            "rolls back whole."
        )


def upgrade():
    """Drop the transaction's transfer column, its keys, the parent-transfer source and the copy."""
    bind = op.get_bind()
    refuse_rows_declaring_a_parent_transfer(bind)
    op.drop_constraint(_ONE_LINK, "transactions", schema="budget", type_="check")
    op.create_check_constraint(
        _ONE_LINK, "transactions", _ONE_LINK_WITHOUT_TRANSFER, schema="budget",
    )
    op.drop_constraint(
        _NAMES_NO_TRANSFER, "transactions", schema="budget", type_="check",
    )
    for name, _columns, _unique, _predicate in reversed(_INDEXES):
        op.drop_index(name, table_name="transactions", schema="budget")
    op.drop_column("transactions", "transfer_id", schema="budget")
    deleted = bind.execute(
        sa.text("DELETE FROM ref.amount_sources WHERE name = :name"),
        {"name": _PARENT_TRANSFER},
    ).rowcount
    copy = "no copy to drop"
    if bind.execute(sa.text(_TWIN_COPY_PRESENT_SQL)).scalar_one():
        kept = bind.execute(
            sa.text(f"SELECT count(*) FROM {_TWIN_COPY}"),
        ).scalar_one()
        op.execute(f"DROP TABLE {_TWIN_COPY}")
        copy = f"{_TWIN_COPY} dropped with its {kept} kept twin(s)"
    print(
        "X-bi-6-5: budget.transactions.transfer_id dropped with its key, "
        f"its two indexes and {_NAMES_NO_TRANSFER}; {_ONE_LINK} re-cut to "
        f"two terms; {deleted} {_PARENT_TRANSFER!r} amount source deleted; "
        f"{copy}; 0 refused."
    )


def downgrade():
    """Restore the column, its keys, the three-term CHECK and the parent-transfer source."""
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO ref.amount_sources (name) "
            "SELECT CAST(:name AS VARCHAR) WHERE NOT EXISTS "
            "(SELECT 1 FROM ref.amount_sources WHERE name = :name)"
        ),
        {"name": _PARENT_TRANSFER},
    )
    op.add_column(
        "transactions",
        sa.Column("transfer_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.create_foreign_key(
        "fk_transactions_transfer_id",
        "transactions", "transfers",
        ["transfer_id"], ["id"],
        source_schema="budget",
        referent_schema="budget",
        ondelete="CASCADE",
    )
    for name, columns, unique, predicate in _INDEXES:
        op.create_index(
            name, "transactions", columns, unique=unique, schema="budget",
            postgresql_where=sa.text(predicate),
        )
    op.drop_constraint(_ONE_LINK, "transactions", schema="budget", type_="check")
    op.create_check_constraint(
        _ONE_LINK, "transactions", _ONE_LINK_WITH_TRANSFER, schema="budget",
    )
    op.create_check_constraint(
        _NAMES_NO_TRANSFER, "transactions", _NAMES_NO_TRANSFER_CONDITION,
        schema="budget",
    )
