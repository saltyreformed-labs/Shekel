"""a ledger entry links only its movement: the transfer and transaction links and kinds go

Revision ID: 1712c506ee99
Revises: c956c1d83108
Create Date: 2026-10-27
Review: developer, 2026-10-27 (ruling plan#1843: X-bi-6-5 is four leaf steps,
the second drops the journal entries' old links and the legacy posting module)
and 2026-10-27 (ruling plan#1867: the migration deletes the 413 cancelled
legacy entries and their two source kinds; destructive: every entry of two
source kinds deleted with its legs, two columns dropped with their foreign
keys and two indexes, two reference rows deleted)

Plan step **balance:X-bi-6-5**, its second leaf, card plan#1845.  A journal
entry linked its source by one of three columns: ``transfer_id`` for the
one-entry ``transfer`` source (Build-Order Step 2), ``transaction_id`` for a
row's own ``transaction`` leg (Step 3), and ``transaction_entry_id`` for a
movement (plan step X-f3b).  Neither older source has been written since
(plan steps ``balance:X-bi-4a`` and ``balance:X-bi-6-3``): the deploy resync
reversed every entry of both to zero, and the reversed pairs it left are the
only entries either column links (ruling **R-BAL102**).  This revision
deletes them and what existed only for them:

  1. **It refuses, writing nothing** (:func:`refuse_what_the_delete_would_move`),
     naming each offending entry or group with its count, its first ids or keys
     and the query that finds the rest, while
     (a) an entry of any OTHER kind carries either link: a state no writer has
     made since ruling **R-BAL102** gave the loan payment split no link, whose
     meaning this revision cannot state, so it refuses rather than drop the
     link silently; or
     (b) the ``transfer`` / ``transaction`` entries of one (scenario, ledger
     account, pay period, entry date, source kind, transfer link, transaction
     link) net to anything but zero.  That key is the finest any reader groups
     them by -- the account walk sums them per period, the loan sync's
     checked projection (``loan_posting_service._linked_ledger._visible_nets``)
     reads every entry on a loan's ledger per DATE, and the deleted readers
     bucketed them per link -- and it is the key the resync reconciled them
     to, per (period, entry date) and per source.  So a pass proves the
     delete moves no figure any reader shows.
  2. It deletes every ``transfer`` and ``transaction`` entry.  Each entry's
     legs go with it by ``ON DELETE CASCADE``; the balanced trigger
     (``ck_account_postings_balanced``) has no DELETE arm, and the audit
     trigger records every deleted row.  The ledger is append-only for the
     app's own writes; this delete is ruling **plan#1867**'s, made once here.
  3. It drops ``idx_journal_entries_transfer`` and
     ``idx_journal_entries_transaction`` by name, so a database missing one
     is refused rather than passed.
  4. It drops ``journal_entries.transfer_id`` and ``transaction_id``, and
     each drop takes the column's foreign key
     (``fk_journal_entries_transfer_id`` / ``fk_journal_entries_transaction_id``).
  5. It deletes the ``transfer`` and ``transaction`` rows of
     ``ref.posting_sources``, which no entry carries any more.  The
     ``transfer`` posting KIND (``ref.posting_kinds``) stays: a transfer
     movement's legs carry it.

**No money moves.**  Step 1 (b) holds every deleted group at zero, and no code
reads either column: plan#1845's first commit deleted the legacy posting module
(``_posting_legacy``), the resync's legacy arm and its refusal (ruling
**R-BAL104**), and every guard and read of the two links in the account walk
and the ledger report.  On the 2026-10-27 14:18 EDT production dump the
delete is 413 entries and their 826 legs, 58 ``transfer`` entries over 19
transfers and 355 ``transaction`` entries over 154 rows, every group at zero.

**The downgrade restores the schema but not the entries** (ruling
**plan#1867**): both reference rows by name (new ids where the old ones were
lost: every revision below resolves them by name), both nullable columns with
their keys, named as before and ``ON DELETE SET NULL``, and both partial
indexes.  The columns come back EMPTY and at the end of the table rather than
where they stood; every revision below reads them by name.  The code below
this revision needs none of the deleted entries: they netted to zero on every
key it reads, its resync reverses a legacy source it finds and finds none, and
its refusal (ruling **R-BAL104**) asks only for a nonzero legacy net.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "1712c506ee99"
down_revision = "c956c1d83108"
branch_labels = None
depends_on = None


#: The two source kinds this revision deletes, by name: a migration resolves a
#: ref row by name because its id differs between databases.
_LEGACY_SOURCES = ("transfer", "transaction")

#: How many offending ids or keys a refusal names; the query finds the rest.
_NAMED_AT_MOST = 20

#: The entries of every OTHER kind that carry either link (step 1 (a)).
_LINKED_ENTRY_OF_ANOTHER_KIND_SQL = """
SELECT je.id
  FROM budget.journal_entries je
  JOIN ref.posting_sources ps ON ps.id = je.source_kind_id
 WHERE ps.name NOT IN :sources
   AND (je.transfer_id IS NOT NULL OR je.transaction_id IS NOT NULL)
 ORDER BY je.id
"""

#: The groups of the two kinds' legs that net nonzero on the finest key any
#: reader reads them by (step 1 (b)), each with its net and its entry count.
_NONZERO_LEGACY_GROUP_SQL = """
SELECT je.scenario_id, p.ledger_account_id, je.pay_period_id, je.entry_date,
       ps.name, je.transfer_id, je.transaction_id,
       sum(p.amount) AS net, count(DISTINCT je.id) AS entries
  FROM budget.journal_entries je
  JOIN ref.posting_sources ps ON ps.id = je.source_kind_id
  JOIN budget.account_postings p ON p.journal_entry_id = je.id
 WHERE ps.name IN :sources
 GROUP BY je.scenario_id, p.ledger_account_id, je.pay_period_id,
          je.entry_date, ps.name, je.transfer_id, je.transaction_id
HAVING sum(p.amount) <> 0
 ORDER BY je.scenario_id, p.ledger_account_id, je.pay_period_id,
          je.entry_date, ps.name, je.transfer_id, je.transaction_id
"""

#: The entries this revision deletes, and their legs, counted for the print.
_LEGACY_ENTRY_COUNT_SQL = """
SELECT count(*)
  FROM budget.journal_entries je
  JOIN ref.posting_sources ps ON ps.id = je.source_kind_id
 WHERE ps.name IN :sources
"""
_LEGACY_LEG_COUNT_SQL = """
SELECT count(*)
  FROM budget.account_postings p
  JOIN budget.journal_entries je ON je.id = p.journal_entry_id
  JOIN ref.posting_sources ps ON ps.id = je.source_kind_id
 WHERE ps.name IN :sources
"""

#: The delete itself: the legs follow by ``ON DELETE CASCADE``.
_DELETE_LEGACY_ENTRIES_SQL = """
DELETE FROM budget.journal_entries
 WHERE source_kind_id IN
       (SELECT id FROM ref.posting_sources WHERE name IN :sources)
"""

#: The two link columns, ``(column, its key, the table it references, its
#: index)``, as ``db239773c2fd`` and ``bdde62675c9b`` created them.
_LINKS = (
    (
        "transfer_id", "fk_journal_entries_transfer_id", "transfers",
        "idx_journal_entries_transfer",
    ),
    (
        "transaction_id", "fk_journal_entries_transaction_id", "transactions",
        "idx_journal_entries_transaction",
    ),
)


def _sources_bound(statement: str) -> sa.TextClause:
    """Return *statement* with ``:sources`` bound to the two legacy kinds' names."""
    return sa.text(statement).bindparams(
        sa.bindparam("sources", value=_LEGACY_SOURCES, expanding=True),
    )


def refuse_what_the_delete_would_move(bind) -> None:
    """Refuse the upgrade while the delete or the drop would lose a fact a reader reads.

    Asked before any write, so a refusal keeps nothing.  Module-level so a
    test can DRIVE it, as ``c956c1d83108``'s refusal is.

    Args:
        bind: A SQLAlchemy connection.

    Raises:
        RuntimeError: Naming each offending entry (an entry of another kind
            carrying a link) or group (the two kinds' legs netting nonzero
            on one key) with its count, its first ids or keys and the query
            that finds the rest.
    """
    found = []
    linked = bind.execute(
        _sources_bound(_LINKED_ENTRY_OF_ANOTHER_KIND_SQL),
    ).scalars().all()
    if linked:
        found.append(
            f"{len(linked)} entry(ies) of a kind other than "
            f"{list(_LEGACY_SOURCES)} carry transfer_id or transaction_id, "
            f"first ids {linked[:_NAMED_AT_MOST]}: no writer has made one "
            "since ruling R-BAL102 and this revision cannot say what the "
            "link meant.  Diagnose with: "
            f"{_LINKED_ENTRY_OF_ANOTHER_KIND_SQL.strip()}"
        )
    groups = bind.execute(_sources_bound(_NONZERO_LEGACY_GROUP_SQL)).all()
    if groups:
        found.append(
            f"{len(groups)} group(s) of {list(_LEGACY_SOURCES)} entries net "
            "nonzero by (scenario, ledger account, pay period, entry date, "
            "source kind, transfer_id, transaction_id, net, entries), first "
            f"{[tuple(group) for group in groups[:_NAMED_AT_MOST]]}: deleting "
            "one moves that account's balance on that day.  Diagnose with: "
            f"{_NONZERO_LEGACY_GROUP_SQL.strip()}"
        )
    if found:
        raise RuntimeError(
            "X-bi-6-5 refuses: "
            + "  ".join(found)
            + f"  Bind :sources to {list(_LEGACY_SOURCES)}.  The previous "
            "release's deploy resync reconciles every legacy source to zero "
            "per (period, entry date); a group it left nonzero is the "
            "developer's to rule before this revision runs.  Nothing was "
            "written: the revision rolls back whole."
        )


def upgrade():
    """Delete the two legacy kinds' entries, then their links, indexes and reference rows."""
    bind = op.get_bind()
    refuse_what_the_delete_would_move(bind)
    entries = bind.execute(_sources_bound(_LEGACY_ENTRY_COUNT_SQL)).scalar_one()
    legs = bind.execute(_sources_bound(_LEGACY_LEG_COUNT_SQL)).scalar_one()
    bind.execute(_sources_bound(_DELETE_LEGACY_ENTRIES_SQL))
    for column, _key, _table, index in _LINKS:
        op.drop_index(index, table_name="journal_entries", schema="budget")
        op.drop_column("journal_entries", column, schema="budget")
    sources = bind.execute(
        _sources_bound("DELETE FROM ref.posting_sources WHERE name IN :sources"),
    ).rowcount
    print(
        f"X-bi-6-5: {entries} legacy journal entry(ies) deleted with their "
        f"{legs} leg(s); budget.journal_entries.transfer_id and "
        "transaction_id dropped with their keys and indexes; "
        f"{sources} posting source(s) {list(_LEGACY_SOURCES)} deleted; "
        "0 refused."
    )


def downgrade():
    """Restore both links, their keys and indexes, and both reference rows; not the entries."""
    bind = op.get_bind()
    for name in _LEGACY_SOURCES:
        bind.execute(
            sa.text(
                "INSERT INTO ref.posting_sources (name) "
                "SELECT CAST(:name AS VARCHAR) WHERE NOT EXISTS "
                "(SELECT 1 FROM ref.posting_sources WHERE name = :name)"
            ),
            {"name": name},
        )
    for column, key, table, index in _LINKS:
        op.add_column(
            "journal_entries",
            sa.Column(column, sa.Integer(), nullable=True),
            schema="budget",
        )
        op.create_foreign_key(
            key,
            "journal_entries", table,
            [column], ["id"],
            source_schema="budget",
            referent_schema="budget",
            ondelete="SET NULL",
        )
        op.create_index(
            index, "journal_entries", [column], unique=False, schema="budget",
            postgresql_where=sa.text(f"{column} IS NOT NULL"),
        )
