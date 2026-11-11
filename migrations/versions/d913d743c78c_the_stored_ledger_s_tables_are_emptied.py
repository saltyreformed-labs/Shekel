"""The stored ledger's tables are emptied: every row of its three tables is deleted

Revision ID: d913d743c78c
Revises: 5f2c46a35a79
Create Date: 2026-11-09 20:06:09

Plan step **balance:X-ew**'s second leaf (plan#2914, task plan#3078), ruling
**plan#3068**.

Review: Josh, 2026-11-09 (plan#3068: "C: empty the tables (Recommended)": a
migration in plan#2914 DELETEs every row of the three tables, the audit log
keeping them, and a rollback refills them from the records).

**What changes.**  Every row of ``budget.account_postings``,
``budget.journal_entries`` and ``budget.ledger_accounts`` is deleted.  The
tables, their reference tables, triggers and permissions stand: X-ew's last
leaf (plan#2915) drops them.

**Why now, rather than with the tables.**  From this leaf no save or deploy
writes the stored ledger and nothing reads it: every reader moved onto the
worked-out ledger (:mod:`app.services.ledger_lines`), which reads the records.
Left in place, production's rows would be a stale copy the suite never holds,
because a test database's store is empty once nothing writes it, so a key on
them could refuse a door in production alone.  One such key is
``fk_ledger_accounts_loan_account_id`` (RESTRICT): each loan's chart rows,
which production holds (measured read-only on production 2026-11-09), refuse
that loan's permanent delete once the door stops clearing them first.  The
measurements below were taken on a copy of production made 2026-11-09; their
figures stay with the measurement, outside this repository (ruling
**R-BAL132**).

**DELETE, not TRUNCATE.**  Each table's audit trigger
(``app.audit_infrastructure``, ``AFTER INSERT OR UPDATE OR DELETE ... FOR EACH
ROW``) fires per deleted row, so ``system.audit_log`` keeps every row this
deletes.  A TRUNCATE fires no row trigger and would leave no trace of them.

**The order: legs, then entries, then chart rows.**  A leg names its entry and
its chart row, so the legs go first and each later DELETE meets no row still
naming one of its own.  Both of a leg's keys are ON DELETE CASCADE, so another
order would delete the same rows, but the legs would then go as a side effect
of another table's statement.  No table outside these three holds a key onto
any of them.  The deferred balanced-journal trigger
(``app.posting_infrastructure``) has no DELETE arm, so removing legs fires
nothing there.

**It needs the owner role, which is the role every migration runs as.**
``shekel_app`` holds no UPDATE or DELETE on ``budget.journal_entries`` and
``budget.account_postings`` (``e3c23fadb21d``, and ``scripts/init_db_role.sql``
at every container start), so this migration is refused under it: on that
copy, with production's grants given ``shekel_app``, its first DELETE failed
``permission denied for table account_postings``.  ``entrypoint.sh`` runs
``scripts/init_database.py``, which runs the migrations, on ``DATABASE_URL``,
the owner's connection; the app runs as ``shekel_app`` only from the gunicorn
handoff on.  That deploy step, run on the copy, emptied the three tables in
its one transaction, and ``system.audit_log`` gained one DELETE row for each
row it deleted.

**The downgrade restores one chart row per account, an account's own.**  A
downgrade comes before an earlier release's image boots, and that release's
deploy hooks (``scripts/init_database.py``, ``_reconcile_the_ledger``) rebuild
every entry, every leg and every other chart row from the records at that
first boot, in the one transaction the migrations commit in.  They never mint
an account's own (``linked``) chart row: a writer finds it and fails loud when
it is missing (``posting_reads._ledger_account_for`` raises ``PostingError``),
since only account create pairs one.  Measured on that copy, with the deploy
step of ``main`` at ``92d2f50e5`` (the release before this leaf) as the
earlier release's first boot, every deploy's clock pinned at one instant:

* on the tables emptied as this upgrade empties them, with nothing restored,
  that step was refused, ``PostingError: No ledger account is linked to
  account 1``, which rolls its whole first boot back;
* after this downgrade (one chart row per account, no entry), the same step
  ran and rebuilt the entries, legs and chart rows from the records, and
  every leg's net by chart row, scenario, paycheck, day, source kind and
  posting kind equals what that step leaves on the copy as it was (its noise
  gate: that step run twice on two clones of the copy, identical).  The chart
  rows are compared by natural key, since the ids differ, and the two the
  rebuild does not mint are an equity and an interest counter whose legs net
  to $0.00 (booked and reversed), which no record books on any longer, so
  they carry no figure.

So the downgrade restores exactly that one kind, and the earlier release
rebuilds the rest from the records.

The pairing rule is restated here as SQL, frozen at this revision, as
``b82538084d24`` spelled it: an account whose type's category is Liability
carries the Liability class, every other account the Asset class (the rule
``app.services.ledger_lines.linked_ledger_class`` states).  The row's name
stays NULL, its label being its account's name, and every other column takes
the shape account create writes.  An account already holding its linked row
keeps it, so a downgrade never duplicates one.  No entry or leg is restored:
they are a derived copy that only the earlier release's writers can compute
(a loan payment's split is a walk over its rate periods and escrow, which no
statement here restates).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "d913d743c78c"
down_revision = "5f2c46a35a79"
branch_labels = None
depends_on = None

#: The three tables, in the order they are emptied: legs, entries, chart rows.
EMPTIED_TABLES = ("account_postings", "journal_entries", "ledger_accounts")

#: One linked chart row for every account that holds none, by the pairing rule
#: frozen at this revision (the module docstring says why it is restated here).
#: ``is_owner_bucket`` and ``created_at`` take their server defaults (``false``,
#: ``now()``), and ``name``, ``category_id`` and ``loan_account_id`` stay NULL,
#: the shape ``ledger_account_service.create_ledger_account_for_account`` writes.
RESTORE_LINKED_ROWS_SQL = """
INSERT INTO budget.ledger_accounts (user_id, class_id, kind_id, account_id)
SELECT a.user_id, lc.id, lk.id, a.id
  FROM budget.accounts a
  JOIN ref.account_types t ON t.id = a.account_type_id
  JOIN ref.account_type_categories cat ON cat.id = t.category_id
  JOIN ref.ledger_account_classes lc
    ON lc.name = CASE WHEN cat.name = 'Liability'
                      THEN 'Liability' ELSE 'Asset' END
  JOIN ref.ledger_account_kinds lk ON lk.name = 'linked'
 WHERE NOT EXISTS (
     SELECT 1 FROM budget.ledger_accounts la
      WHERE la.account_id = a.id AND la.kind_id = lk.id
 )
 ORDER BY a.id
"""


def upgrade():
    """DELETE every row of the stored ledger's three tables, legs first.

    Runs as the owner role; see the module docstring for why each row is
    deleted rather than truncated, and for the order.
    """
    for table in EMPTIED_TABLES:
        op.execute(f"DELETE FROM budget.{table}")


def downgrade():
    """Restore one linked chart row per account, so an earlier release can rebuild the rest.

    The earlier release's deploy hooks re-post every entry and mint every
    other chart row from the records at its first boot; the module docstring
    gives the measurement and why no entry is restored here.
    """
    op.execute(RESTORE_LINKED_ROWS_SQL)
