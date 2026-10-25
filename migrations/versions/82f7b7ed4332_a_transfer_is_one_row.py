"""a transfer is one row: its twin rows are deleted and refused

Revision ID: 82f7b7ed4332
Revises: e616adf7fe22
Create Date: 2026-10-09
Review: developer, 2026-09-30 (ruling R-BAL166: the twins are deleted in the
release that stops their upkeep) and 2026-10-09 (ruling R-BAL258: the database
refuses a new twin; ruling R-BAL260: the downgrade restores each twin from a
kept copy)

Plan step **balance:X-bi-6-4d-2** of ``docs/audits/balance_architecture/README.md``
(design ``X-bi-6-4d`` D6).  **Every transfer had two hidden twin
``budget.transactions`` rows**, one on each account, which the transfer service
created with it and kept equal to it.  Since ``e616adf7fe22`` (the revision
before it, in the same plan step and release) each side's payment record hangs
off the transfer by a side
link and nothing reads a twin, so this revision deletes them:

  1. **It refuses, writing nothing, while a twin could not go cleanly**, each
     named with the query that finds it: a twin any foreign key still names
     (every key into ``budget.transactions``, read from the catalog: a journal
     entry, a statement-match creation, a movement or a card payback at this
     revision -- a delete would null, cascade or refuse each); a transfer whose
     twins are not exactly one expense and one income (Transfer Invariant 1
     broken; the kept copy could not then be restored as a pair); a twin
     whose COPIED fields (:data:`_COPIED_FIELDS`) are not its transfer's,
     named as ``(transfer, twin, [fields])``; a twin filed under a
     category that is not its owner's, named as ``(transfer, twin,
     category, that category's owner, the transfer's own category, THAT
     category's owner)``; or a transfer filed under a category that is not
     its owner's, named as ``(transfer, category, that category's owner,
     the transfer's owner)`` (below).  The step's premise is that a
     twin is a copy of its transfer, so deleting it loses nothing; a twin
     that disagrees is no copy, and its delete would erase the last trace of
     the disagreement unseen.  A field is refused only where the app at
     ``e616adf7fe22`` (the X-bi-6-4d-2 leaf, commit ``5f46d6973``, in whose
     transfer service every write of a transfer is made) still writes it on
     both twins with the transfer; each writer, at its file and line in
     ``app/services/transfer_service/`` at that commit:

     - ``user_id``: the create door copies the owner (``_create.py:171``),
       and no door writes a transfer's owner after;
     - ``account_id``: the create door (``_create.py:172``) and the endpoint
       move (``_endpoints.py:320-326``), each twin its side's endpoint;
     - ``pay_period_id``: the create door (``_create.py:175``), the update
       (``_update.py:327-329``) and the restore (``_restore.py:140``);
     - ``scenario_id``: the create door (``_create.py:176``), and no door
       writes a transfer's scenario after;
     - ``due_date``: the create door (``_create.py:191``), the update
       (``_update.py:379-381``) and the restore (``_restore.py:169``);
     - ``is_override``: the create door (``_create.py:187``), the update
       (``_update.py:555-556``) and the restore (``_restore.py:183``);
     - ``is_deleted``: the create door (``_create.py:188``), the soft delete
       (``_delete.py:167-171``) and the restore (``_restore.py:109-113``).

     **``status_id`` is not refused.**  From ``e616adf7fe22`` the status
     seam's Transfer arm writes the transfer and its sides' records and never
     a twin (``_status.py:287-288`` at that commit; that revision's docstring,
     "What the downgrade does NOT restore"), so after a settle, revert or
     cancel made there a twin's status is not its transfer's, by design, and
     refusing it would wedge a database the app wrote there in both
     directions (this revision's upgrade refusing, ``e616adf7fe22``'s
     downgrade refusing).  The status the premise needs is asked one revision
     earlier, where the twin still is its copy: ``e616adf7fe22``'s upgrade
     refuses a twin whose status, settle day, day basis or statement link is
     not its transfer's and its side's record's, and the release runs the two
     upgrades in one transaction.  Every count is 0 where the transfer
     service wrote the rows.  A transfer holding NO twin passes: it is
     already the shape this revision makes (the create door at this
     revision, run against a database still at ``e616adf7fe22``, writes
     one), and the downgrade builds its two.

     **A twin filed under a category that is not its owner's is refused**
     because no door writes one, so it too is a disagreement its delete
     would carry into the copy unseen.  At ``5f46d6973``: every door naming
     a transfer's category refuses one that is not the owner's
     (``_get_owned_category``, ``_ownership.py:149``, asked by the create
     door, ``_create.py:363``, and by the update's
     ``_reject_unowned_references``, ``_update.py:386``); the twins took
     their transfer's category (``_create.py:179``, ``_update.py:334-338``,
     ``_restore.py:147-154``) or, before the create door copied it, their
     owner's own "Transfers: Incoming" (commit ``0fea29962``, finding
     BAL-575) and, on the expense side of a transfer with no category, their
     owner's own "Transfers: Outgoing" (``app/services/transfer_service.py``
     ``:403`` and ``:548-550`` at ``0fea29962``, removed at ``f99fe7d7d``);
     the one writer outside the doors, ``scripts/repair_orphaned_transfers.py``
     (added at ``f99fe7d7d``, deleted at ``22add0947``), wrote the transfer's
     category or those two of its owner's (``:85-97`` at ``22add0947~1``);
     and a category's owner is written only when it is created
     (``app/routes/categories.py:88``, ``app/routes/salary/profiles.py:182``,
     ``app/services/registration_service.py:499``,
     ``app/services/credit_workflow.py:600``), its edit writing the names
     alone (``app/routes/categories.py:157-158``).  Refused here, a kept
     category that is not its twin's owner's at the downgrade is one where
     something CHANGED since (ruling R-BAL261, below).  The category is not
     otherwise compared with the transfer's: those older income twins name
     one their transfer does not, so it is no copied field.

     **A transfer filed under a category that is not its owner's is refused
     too** (the cp2a review's L-1, option D, decided by the coordinator
     2026-10-11).  No door writes one (the doors above), and the
     downgrade's one build rule files every twin it builds under its
     transfer's category (:data:`_BUILT_SQL`), so the twins of such a
     transfer, rebuilt once it is written since or built where the copy
     holds none of them, would come back under another user's category,
     which the twin refusal of the next upgrade names: asked here, the
     first upgrade refuses the transfer before any twin of it can come back
     so (a twin-only repair passed this upgrade before it asked).  Each twin
     refused for its category is therefore named with its transfer's own
     category and THAT category's owner beside its own, and the repair is
     read off that owner: where the transfer names a category whose owner
     is not the transfer's, the transfer is refused as well and is a row to
     repair, its twins with it (a repair of the twins alone leaves the
     transfer's own refusal standing); otherwise (the transfer names its
     owner's category, or none) the twin alone is, whatever moved (its
     category, that category's owner, or its own owner, which the
     copied-field refusal names too).
  2. **It keeps a copy of every twin** in ``system.transfer_twin_purge``: the
     whole row as ``jsonb`` (the shape ``b2d8f3a6c541``'s
     ``system.pre_origination_purge`` keeps), with its transfer's version
     counter and whether the twin's side held a payment record at that moment,
     which the downgrade reads (ruling **R-BAL260**, "Keep a copy").
  3. **It deletes every twin** and adds ``ck_transactions_names_no_transfer``
     (``transfer_id IS NULL``), so a row naming a transfer is unstorable from
     here on (ruling **R-BAL258**, "Database refuses").  Plan step
     ``X-bi-6-5`` drops ``transactions.transfer_id`` with this CHECK, its
     index, its partial unique index, its foreign key and its term in
     ``ck_transactions_one_pricing_link``.

**No money moves.**  No balance reads a twin (Transfer Invariant 5: a
projected leg is drawn off its transfer and a settled one is its side's dated
movement), and a twin held no record (``e616adf7fe22`` moved every one onto
its side link).  A ``$0.00`` close's twins alone held that close's day, basis
and statement link, which no reader at this revision reads (the close is its
transfer's settled status and no movement) and which the copy keeps for the
downgrade.  The deletes write audit rows, as any delete does.  A twin's
FIGURE was its transfer's: each was born declaring ``parent_transfer`` and
storing none, and the update below this revision wrote a figure the owner
typed onto both legs as well (``_amount.apply_amount_ownership``).  The
category "Transfers: Incoming" that older income twins carried in place of
their transfer's own (finding **BAL-575**) is no longer used by any row it
was used only by, so the category screen may then delete it; the downgrade
then files those twins under their transfer's category (ruling **R-BAL261**,
below).

**The downgrade restores the kept copy EXACTLY where nothing has changed**
(ruling **R-BAL260**): a twin whose transfer was not written since the upgrade
comes back with every column it was kept with -- its own id, creation and
update times, version counter, type, name, category, figure and day -- save
what its transfer and its side's record hold NOW, which is read live whatever
the kept twin held: the mirrored fields (:data:`_MIRRORED_FIELDS`, status among
them) from the transfer, and the side's payment record's day, basis and link
when the side holds one now (a ``$0.00`` close kept with its day keeps it while
its side still holds no record and its transfer is still in the settled status
it was kept in).  So on a database no app wrote at ``e616adf7fe22`` --
production's release runs the two upgrades in one transaction -- a downgrade
straight after the upgrade reproduces every twin column for column:
``e616adf7fe22``'s upgrade let through only twins whose status, day, basis and
link were their transfer's and their record's, and this one only twins whose
copied fields were their transfer's and whose category was their owner's; and
an older revision's own downgrade that
finds a twin by its id (``c4e91a7b2d38``'s ``system.loan_due_date_backfill``)
still finds it.  On a database the app DID write at ``e616adf7fe22``, a twin of
a transfer it settled, reverted or cancelled there, or soft-deleted there
while settled, or whose side's record it re-dated, re-stated on a better day
basis or linked to a statement there, comes back different from its kept row:
a settle, revert or cancel made there wrote the transfer and its side's
records and never the twin; a soft delete of a settled transfer took its
sides' records off and returned the transfer alone to Projected
(``_delete.py:160-178`` at ``5f46d6973``, ruling R-BAL246); and a day
correction, a basis-only re-statement (``status_seam/_side.py:252-253,
291-293``; an endpoint move's moved side among them, whose day becomes
``borrowed``, ``_endpoints.py:280-284, 311-329``) or a statement link wrote
the record alone.  The live read also answers what the version
counter below cannot see: a raw statement or a later revision that wrote the
transfer without raising its counter, and the record, which is a row of its
own.  Such a twin comes back saying what its transfer and record say now -- the
code below this revision reads a twin for exactly that, and ``e616adf7fe22``'s
downgrade, which runs next, refuses one that does not -- and the downgrade
prints how many came back different from their kept row.  Only the mirrored
fields answer a change of the transfer this way: its label, category and figure
come back as kept (save a kept category that no longer fits, below).  The kept
day's two guards (the twin was kept in its transfer's present status, and its
side held no record then) answer only such changes: a raw statement on the
transfer's status, and a record removed without writing its transfer.  The
figure (``estimated_amount``, ``amount_source_id``) is not a mirrored field: it
is neither refused nor re-read, so an unwritten transfer's twins bring back
exactly the figure the copy kept, whatever it was.

**A twin whose transfer WAS written since is brought in line with it** by the
ONE rule that also builds the twins of a transfer created since
(:data:`_BUILT_SQL`, the create door below this revision): its side's endpoint
and label ("Transfer to <to>" on the from-side, "Transfer from <from>" on the
to-side), the transfer's period, scenario, status, due date, flags and
category (on BOTH sides), a DERIVED figure (``parent_transfer``, nothing
stored), and its side's record's day, basis and link, or none.  The transfer's
version counter, kept in the copy, is the test of "written since": every ORM
flush that changes a transfer raises it (``version_id_col``) and the app
holds no bulk transfer update, so it sees what the code below wrote onto both
twins at a full-edit save, a restore, or an A-to-B-to-A edit, which comparing
the transfer's values with the kept ones cannot.  A save that changed nothing
on its transfer writes no row and leaves no trace, so its twins come back as
kept: nothing changed on the transfer for ruling R-BAL260 to bring them in
line with.  A derived figure prices exactly
what an owned copy of the parent's would (a leg reads its parent) and cannot
disagree with it; a ``$0.00`` close made after the upgrade has no day anywhere
(ruling **R-BAL230**), so its twins come back settled with no day, the
dateless settled pair the code below calls legacy (finding **N-181**).  **Such
a twin keeps its kept id, creation and update times and version counter while
its content changes.**  That is safe because no form below this revision
pins a TWIN's counter: the transaction doors refuse a row naming a transfer
(Transfer Invariant 4), and the transfer popovers pin the TRANSFER's counter,
so no stale twin counter can be compared against the rebuilt row.

**A kept value that no longer fits comes back as the transfer says** (ruling
**R-BAL261**, "Use the transfer's").  The upgrade releases what the twins held
in place, so an ordinary act can leave a kept value pointing at nothing.  A
kept category no longer fits, and comes back as the transfer's category, when
it is gone (the category screen has since deleted it) or is not its twin's
owner's now.  The upgrade refused every twin whose category was not its
owner's, so the second is reached only where something CHANGED since: the
twin's owner (the mirrored fields re-read it from the transfer) or the
category's own owner, each moved only by a raw statement (no door moves a
transfer between users or writes a category's owner after its create, and no
key on a row's category asks its owner).  Where nothing changed, the category
still there and still its twin's owner's, it comes back as kept.  **The
fallback can fit no better.**  The upgrade refuses a transfer whose OWN
category is not its owner's, so one met at the downgrade was made so by a raw
statement, since the upgrade or on a database built at this revision, which
never ran it: the transfer moved without it, the category given to another
user, or the transfer written under another user's.  A twin then
comes back under it as its transfer says, whether by this fallback, by the
rebuild of a transfer written since, or by the build for a transfer the copy
holds none of (one created since, one the upgrade met holding none, or every
transfer where the copy is missing), and the next upgrade refuses that twin
by name with its transfer's category and that category's owner beside it,
and the transfer for its own category: the transfer is the row to repair,
its twins with it.  A kept
``$0.00``-close day that now falls on or before its account's books opening
(an opening restated after the upgrade) comes back with no day, basis or
link; a kept statement link whose statement is gone comes back with none.
The downgrade prints how many twins came back so, counting a twin only where
a fallback changed its value: the fallback can be the very category kept (the
create door's own shape, both twins under their transfer's category), which
changes nothing.

``e616adf7fe22``'s downgrade, which runs next, requires exactly this: every
twin saying its transfer's status, and its side's record's day, basis and link
unless it is a twin of a settled transfer whose side holds no record (a
``$0.00`` close, read for its status alone).  A transfer created after the
upgrade has no kept twins, so it gets two new ones by the same rule (new ids,
version 1, created when the transfer was, updated at the downgrade).  A kept
twin whose transfer was since hard-deleted is not restored: the code below
this revision would have cascaded it away with its transfer.  The copy is
dropped last.

**A missing copy is read as an empty one**: the downgrade restores none and
builds two twins for every transfer, so the undo finishes (ruling R-BAL261).
A database built AT this revision (``scripts/init_database.py``:
``create_all``, then a stamp) never ran the upgrade and so holds no copy and
no twin.  The downgrade cannot tell that database from one that ran the
upgrade and LOST its copy since (dropped by hand, a partial restore, a later
revision), so its print says only that no copy table was found.  **For plan
step X-bi-6-5, which decides the copy's fate: dropping the copy turns this
downgrade's exact restore into a rebuild of every twin, with no error** --
new ids, the transfer's category, derived figures, no ``$0.00``-close day --
and ``c4e91a7b2d38``'s downgrade, which writes old due dates back by the twin
ids its backfill table names, then updates no twin.

``tests/test_models/test_a_transfer_is_one_row.py`` drives the shipped
``upgrade`` / ``downgrade``, each refusal (the drift refusal field by field,
the twin's category refusal over a live and a soft-deleted transfer, each on
each side, and the transfer's over a live, a soft-deleted and a twinless
one), a database the app wrote at
``e616adf7fe22`` taking that revision's documented way down, the exact round
trip, the live re-read of each mirrored field, the rebuild of a written
transfer's twins, and each value that no longer fits.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "82f7b7ed4332"
down_revision = "e616adf7fe22"
branch_labels = None
depends_on = None


#: The INCOME transaction type's id, by name: a migration resolves a ref row by
#: name because its id differs between databases.  An income twin is the
#: to-side; every other twin is the from-side (``e616adf7fe22``'s rule).
_INCOME = "(SELECT id FROM ref.transaction_types WHERE name = 'Income')"

#: The EXPENSE transaction type's id, by name, for a twin built new.
_EXPENSE = "(SELECT id FROM ref.transaction_types WHERE name = 'Expense')"

#: The ``parent_transfer`` amount source, by name: every twin declared it.
_PARENT_TRANSFER = (
    "(SELECT id FROM ref.amount_sources WHERE name = 'parent_transfer')"
)

#: How many offending rows a refusal names; the diagnostic query finds the rest.
_NAMED_AT_MOST = 20

#: Every foreign key into ``budget.transactions.id``, read from the CATALOG:
#: ``(referencing table, its column)``, each quoted for SQL.  A delete of a
#: twin would set the referencing column NULL, cascade the referencing row away
#: or be refused, so a twin any of them names refuses the upgrade instead.
#: Read rather than listed so a key the schema gains is covered without a
#: second list to keep in step with it; a composite key contributes the column
#: paired with ``id``.  DISTINCT because one column can carry two keys
#: (``transaction_entries.transaction_id`` holds a single-column key and the
#: composite owner key), and one reference is one reference.
_FOREIGN_KEYS_INTO_TRANSACTIONS_SQL = """
SELECT DISTINCT quote_ident(n.nspname) || '.' || quote_ident(cl.relname),
       quote_ident(a.attname)
  FROM pg_constraint c
  JOIN pg_class cl ON cl.oid = c.conrelid
  JOIN pg_namespace n ON n.oid = cl.relnamespace
  CROSS JOIN LATERAL unnest(c.conkey, c.confkey) AS k(local, referenced)
  JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.local
  JOIN pg_attribute r ON r.attrelid = c.confrelid AND r.attnum = k.referenced
 WHERE c.contype = 'f'
   AND c.confrelid = 'budget.transactions'::regclass
   AND r.attname = 'id'
 ORDER BY 1, 2
"""


def foreign_keys_into_transactions(bind) -> list[tuple[str, str]]:
    """Return every ``(table, column)`` whose foreign key names a transaction.

    Module-level so a test can grade the catalog read itself: a query that
    found nothing would leave the refusal below asking nothing.

    Args:
        bind: A SQLAlchemy connection.

    Returns:
        Each referencing table (schema-qualified) and column, quoted for SQL,
        in name order.
    """
    return [
        (table, column) for table, column in bind.execute(
            sa.text(_FOREIGN_KEYS_INTO_TRANSACTIONS_SQL),
        )
    ]


def referenced_twins_sql(bind) -> str:
    """Return the query naming every twin a foreign key still points at.

    Its rows are ``(twin id, transfer id, "table.column" naming it)``.  One
    arm per key :func:`foreign_keys_into_transactions` reads, every arm the
    same ``EXISTS`` over its own column.

    Args:
        bind: A SQLAlchemy connection.

    Returns:
        The query, ready to run.
    """
    arms = " UNION ALL ".join(
        f"SELECT '{table}.{column}' AS what WHERE EXISTS "
        f"(SELECT 1 FROM {table} r WHERE r.{column} = t.id)"
        for table, column in foreign_keys_into_transactions(bind)
    )
    return (
        "SELECT t.id AS twin_id, t.transfer_id, r.what "
        "FROM budget.transactions t "
        f"JOIN LATERAL ({arms}) r ON TRUE "
        "WHERE t.transfer_id IS NOT NULL ORDER BY t.id, r.what"
    )


#: Every transfer holding twins that are not exactly one expense and one
#: income, live or deleted: ``(transfer id, expense twins, income twins)``.  A
#: transfer holding none is not one of them (the module docstring, item 1).
_UNPAIRED_SQL = f"""
SELECT x.id,
       count(t.id) FILTER (WHERE t.transaction_type_id <> {_INCOME}),
       count(t.id) FILTER (WHERE t.transaction_type_id = {_INCOME})
  FROM budget.transfers x
  JOIN budget.transactions t ON t.transfer_id = x.id
 GROUP BY x.id
HAVING count(t.id) FILTER (WHERE t.transaction_type_id <> {_INCOME}) <> 1
    OR count(t.id) FILTER (WHERE t.transaction_type_id = {_INCOME}) <> 1
 ORDER BY x.id
"""

_CREATE_COPY_SQL = """
CREATE TABLE system.transfer_twin_purge (
    row_id integer PRIMARY KEY,
    transfer_id integer NOT NULL,
    row_data jsonb NOT NULL,
    transfer_version_id integer NOT NULL,
    side_held_record boolean NOT NULL
)
"""

_COMMENT_COPY_SQL = """
COMMENT ON TABLE system.transfer_twin_purge IS
'Every transfer twin row (budget.transactions with transfer_id set) as revision
82f7b7ed4332 (plan step balance:X-bi-6-4d-2, ruling R-BAL260) deleted it: the
whole row as jsonb, its transfer''s version counter and whether the twin''s side
held a payment record at that moment.  Read by that revision''s downgrade,
which restores each twin from it, and inherited by plan step balance:X-bi-6-5,
which decides its fate when it drops transactions.transfer_id.'
"""

#: The copy: each twin whole, its transfer's version counter (the downgrade's
#: test of "written since"), and whether its side held a payment record (a side
#: link on the twin's side of its transfer).
_KEEP_COPY_SQL = f"""
INSERT INTO system.transfer_twin_purge
       (row_id, transfer_id, row_data, transfer_version_id, side_held_record)
SELECT t.id, t.transfer_id, to_jsonb(t), x.version_id,
       EXISTS (
         SELECT 1 FROM budget.transaction_entries e
          WHERE CASE WHEN t.transaction_type_id = {_INCOME}
                     THEN e.income_transfer_id
                     ELSE e.expense_transfer_id END = x.id
       )
  FROM budget.transactions t
  JOIN budget.transfers x ON x.id = t.transfer_id
"""

_DELETE_TWINS_SQL = "DELETE FROM budget.transactions WHERE transfer_id IS NOT NULL"

#: Whether the copy is missing: a database built AT this revision by
#: ``scripts/init_database.py`` never ran the upgrade that creates it (the
#: module docstring's last paragraph).
_COPY_ABSENT_SQL = "SELECT to_regclass('system.transfer_twin_purge') IS NULL"

#: THE ONE RULE for a twin the downgrade writes from its transfer: each side of
#: each transfer as the create door below this revision built it, with the
#: side's record's day, basis and link (``e616adf7fe22``'s contract).  Both
#: restore paths read it -- a kept twin whose transfer was written since takes
#: every column it states, a transfer created since gets two rows of it -- so
#: "in line with its transfer" is spelled once.  ``twin`` holds every column of
#: ``budget.transactions`` but the four a row's identity is made of (``id``,
#: ``created_at``, ``updated_at``, ``version_id``), which each path states.
_BUILT_SQL = f"""
SELECT x.id AS transfer_id, side.is_income, side.account_id, x.status_id,
       x.version_id, x.created_at, e.id IS NOT NULL AS has_record,
       jsonb_build_object(
         'user_id', x.user_id,
         'account_id', side.account_id,
         'template_id', NULL,
         'pay_period_id', x.pay_period_id,
         'scenario_id', x.scenario_id,
         'status_id', x.status_id,
         'name', side.label,
         'category_id', x.category_id,
         'transaction_type_id', side.type_id,
         'estimated_amount', NULL,
         'amount_source_id', {_PARENT_TRANSFER},
         'is_override', x.is_override,
         'is_deleted', x.is_deleted,
         'transfer_id', x.id,
         'credit_payback_for_id', NULL,
         'notes', NULL,
         'due_date', x.due_date,
         'occurs_on', NULL,
         'settled_on', e.settled_on,
         'settled_day_basis_id', e.settled_day_basis_id,
         'reconciled_by_id', e.reconciled_by_id
       ) AS twin
  FROM budget.transfers x
  JOIN budget.accounts fa ON fa.id = x.from_account_id
  JOIN budget.accounts ta ON ta.id = x.to_account_id
  CROSS JOIN LATERAL (
        VALUES (FALSE, x.from_account_id, 'Transfer to ' || ta.name,
                {_EXPENSE}),
               (TRUE, x.to_account_id, 'Transfer from ' || fa.name,
                {_INCOME})
       ) side(is_income, account_id, label, type_id)
  LEFT JOIN budget.transaction_entries e
         ON CASE WHEN side.is_income THEN e.income_transfer_id
                 ELSE e.expense_transfer_id END = x.id
"""

#: The COPIED fields, which the UPGRADE refuses a twin over: the columns the
#: app at ``e616adf7fe22`` still writes on both twins with their transfer (its
#: side's endpoint for ``account_id``; the module docstring names each
#: writer), each as :data:`_BUILT_SQL` states it.  Only a field that app keeps
#: equal is evidence of a drift when it differs.  Save ``category_id``: that
#: app writes it on both twins too (``_create.py:179``, ``_update.py:334-338``,
#: ``_restore.py:147-154`` at ``5f46d6973``), but older income twins carry
#: "Transfers: Incoming" in place of their transfer's (finding BAL-575), so on
#: production it is no copy, and refusing its difference would refuse
#: production: the upgrade asks only that it be its owner's
#: (:data:`_FOREIGN_CATEGORY_SQL`), and ruling R-BAL261 (the module docstring)
#: says when a kept one comes back otherwise.
_COPIED_FIELDS = (
    "user_id", "account_id", "pay_period_id", "scenario_id", "due_date",
    "is_override", "is_deleted",
)

#: The MIRRORED fields, which the DOWNGRADE re-reads from the transfer: every
#: column a twin took from it -- the copied fields, and ``status_id``, which
#: the app at ``e616adf7fe22`` writes on the transfer alone.  Every one is
#: re-read because the code below this revision reads a twin for what its
#: transfer says, and ``e616adf7fe22``'s downgrade, next, refuses a twin whose
#: status is not its transfer's.  Save ``category_id``, which a twin took from
#: it too: older income twins carry another (finding BAL-575), so it is not
#: re-read, and an unwritten transfer's twin keeps its kept one unless it no
#: longer fits (ruling R-BAL261).
_MIRRORED_FIELDS = _COPIED_FIELDS + ("status_id",)

#: One arm per copied field: its name when the twin's value (``r.twin``) is
#: not what the build rule says (``b.twin``).  Both are ``jsonb`` read the
#: same way, so a ``NULL`` due date on both is no difference.
_MIRROR_DRIFT_ARMS = ", ".join(
    f"CASE WHEN r.twin->'{field}' IS DISTINCT FROM b.twin->'{field}' "
    f"THEN '{field}' END"
    for field in _COPIED_FIELDS
)

#: Every twin whose copied fields are not its transfer's, each compared with
#: what the ONE build rule says that side's twin holds: ``(transfer id, twin
#: id, the fields that differ)``, the fields in :data:`_COPIED_FIELDS`'
#: order.
_MIRROR_DRIFT_SQL = f"""
SELECT transfer_id, twin_id, fields FROM (
SELECT t.transfer_id, t.id AS twin_id,
       array_remove(ARRAY[{_MIRROR_DRIFT_ARMS}], NULL) AS fields
  FROM budget.transactions t
  CROSS JOIN LATERAL (SELECT to_jsonb(t) AS twin) r
  JOIN ({_BUILT_SQL}) b
    ON b.transfer_id = t.transfer_id
   AND b.is_income = (t.transaction_type_id = {_INCOME})
) twins
 WHERE cardinality(fields) > 0
 ORDER BY transfer_id, twin_id
"""

#: Every twin filed under a category that is not its owner's: ``(transfer id,
#: twin id, the category, that category's owner, the transfer's own
#: category, THAT category's owner)``.  No door writes one (the module
#: docstring, item 1).  The last, read against the transfer's owner, says
#: which row to repair: where the transfer names a category whose owner is
#: not the transfer's, :data:`_FOREIGN_TRANSFER_CATEGORY_SQL` names the
#: transfer too, and the transfer is a row to repair, its twins with it;
#: otherwise (the transfer names its owner's category, or none) the twin
#: alone is.  The tuple does not carry the transfer's owner; the transfer
#: refusal's tuple does (fourth), and its diagnostic query lists every
#: transfer it applies to.  The join to its transfer finds the twins (the
#: key on ``transfer_id`` makes it total over them; a migrated database
#: names it ``fk_transactions_transfer_id``, while the model declares it
#: unnamed); a twin with no category names none, and
#: ``transactions_category_id_fkey`` makes the join total over the rest.  The
#: transfer's category is LEFT joined: a transfer may name none.
_FOREIGN_CATEGORY_SQL = """
SELECT t.transfer_id, t.id, t.category_id, c.user_id, x.category_id,
       xc.user_id
  FROM budget.transactions t
  JOIN budget.transfers x ON x.id = t.transfer_id
  JOIN budget.categories c ON c.id = t.category_id
  LEFT JOIN budget.categories xc ON xc.id = x.category_id
 WHERE c.user_id <> t.user_id
 ORDER BY t.transfer_id, t.id
"""

#: Every transfer filed under a category that is not its owner's, live or
#: soft-deleted: ``(transfer id, the category, that category's owner, the
#: transfer's owner)``.  No door writes one (the module docstring, item 1),
#: and the downgrade's build rule files every twin it builds under its
#: transfer's category, a soft-deleted transfer's among them.  A transfer
#: naming no category names none, and the transfers' category key makes the
#: join total over the rest.
_FOREIGN_TRANSFER_CATEGORY_SQL = """
SELECT x.id, x.category_id, c.user_id, x.user_id
  FROM budget.transfers x
  JOIN budget.categories c ON c.id = x.category_id
 WHERE c.user_id <> x.user_id
 ORDER BY x.id
"""

#: The unwritten branch's re-read of every mirrored field from the build rule,
#: as ``jsonb_build_object`` arguments.
_MIRRORED_REREAD = ", ".join(
    f"'{field}', b.twin->'{field}'" for field in _MIRRORED_FIELDS
)

#: Each kept twin whose transfer still exists, back into the table (the module
#: docstring's rules), in ONE statement that also counts what it did:
#: ``(restored, rebuilt because written since, of an unwritten transfer and
#: different from its kept row, of those because a kept value no longer
#: fits)``.  ``gate`` asks "written since" once (the transfer's version counter
#: against the kept one); ``kept`` reads the copy's values once; ``fit`` asks
#: each of ruling R-BAL261's questions once.  A kept category fits while it
#: exists and is its twin's owner's (the owner the build rule states, which
#: the unwritten branch re-reads); the upgrade refused one that was not, so
#: one unfit now is one whose category or owner changed since.  A kept
#: ``$0.00``-close day fits while ``budget.books_hold`` admits it on its
#: account (the comparison ``ck_movement_after_books_open`` asks at COMMIT),
#: and a kept link while its statement still exists on that account (the pair
#: ``fk_transactions_reconciled_by`` names; a statement never changes
#: accounts, so the account term only states the key's own question).  A
#: written transfer's twins take the transfer's category whatever the kept one
#: was, so ``category_unfit`` is asked of an unwritten one alone.  ``unfit``
#: counts a twin only where a fallback CHANGED its value, so the printed count
#: names only the twins a fallback actually changed: an unfit day and a gone
#: link always come back ``NULL`` over a kept value, but the transfer's
#: category can be the very one kept (a twin filed under its transfer's
#: category, then moved to another user with the transfer, the category left
#: behind, or the category given to another user), so the category arm counts
#: only where the two differ.  The fallback can fit no better: where the
#: transfer's category is not its owner's either, the twin comes back under
#: it as its transfer says and the next upgrade refuses it and its transfer by
#: name, the transfer being the row to repair, its twins with it (ruling
#: R-BAL261, the module docstring).
_RESTORE_KEPT_SQL = f"""
WITH staged AS (
    SELECT p.row_id, gate.written, p.row_data AS kept_row,
           (fit.category_unfit
            AND b.twin->'category_id' IS DISTINCT FROM p.row_data->'category_id')
           OR fit.day_unfit OR fit.link_gone AS unfit,
           p.row_data || CASE
               WHEN gate.written THEN b.twin
               ELSE jsonb_build_object(
                   {_MIRRORED_REREAD},
                   'category_id', CASE WHEN fit.category_unfit
                                       THEN b.twin->'category_id'
                                       ELSE p.row_data->'category_id' END,
                   'settled_on', CASE
                       WHEN b.has_record THEN b.twin->'settled_on'
                       WHEN kept.day_applies AND NOT fit.day_unfit
                           THEN p.row_data->'settled_on' END,
                   'settled_day_basis_id', CASE
                       WHEN b.has_record THEN b.twin->'settled_day_basis_id'
                       WHEN kept.day_applies AND NOT fit.day_unfit
                           THEN p.row_data->'settled_day_basis_id' END,
                   'reconciled_by_id', CASE
                       WHEN b.has_record THEN b.twin->'reconciled_by_id'
                       WHEN kept.day_applies AND NOT fit.day_unfit
                            AND NOT fit.link_gone
                           THEN p.row_data->'reconciled_by_id' END
               ) END AS row_data
      FROM system.transfer_twin_purge p
      JOIN ({_BUILT_SQL}) b
        ON b.transfer_id = p.transfer_id
       AND b.is_income = ((p.row_data->>'transaction_type_id')::integer
                          = {_INCOME})
      JOIN ref.statuses s ON s.id = b.status_id
      CROSS JOIN LATERAL (
            SELECT p.transfer_version_id <> b.version_id AS written
           ) gate
      CROSS JOIN LATERAL (
            SELECT (NOT gate.written
                    AND NOT b.has_record
                    AND NOT p.side_held_record
                    AND s.is_settled
                    AND (p.row_data->>'status_id')::integer = b.status_id)
                   AS day_applies,
                   (p.row_data->>'category_id')::integer AS category_id,
                   (p.row_data->>'settled_on')::date AS settled_on,
                   (p.row_data->>'reconciled_by_id')::integer
                   AS reconciled_by_id
           ) kept
      CROSS JOIN LATERAL (
            SELECT NOT gate.written
                   AND kept.category_id IS NOT NULL
                   AND NOT EXISTS (
                         SELECT 1 FROM budget.categories c
                          WHERE c.id = kept.category_id
                            AND c.user_id = (b.twin->>'user_id')::integer
                       ) AS category_unfit,
                   kept.day_applies
                   AND kept.settled_on IS NOT NULL
                   AND NOT COALESCE(budget.books_hold(
                         budget.account_books_opened_on(b.account_id),
                         kept.settled_on
                       ), TRUE) AS day_unfit
           ) fit_day
      CROSS JOIN LATERAL (
            SELECT fit_day.category_unfit, fit_day.day_unfit,
                   kept.day_applies
                   AND NOT fit_day.day_unfit
                   AND kept.reconciled_by_id IS NOT NULL
                   AND NOT EXISTS (
                         SELECT 1 FROM budget.account_anchor_history h
                          WHERE h.id = kept.reconciled_by_id
                            AND h.account_id = b.account_id
                       ) AS link_gone
           ) fit
), restored AS (
    INSERT INTO budget.transactions
    SELECT r.*
      FROM staged s
      CROSS JOIN LATERAL jsonb_populate_record(
            NULL::budget.transactions, s.row_data
           ) r
     ORDER BY s.row_id
    RETURNING 1
)
SELECT (SELECT count(*) FROM restored),
       count(*) FILTER (WHERE s.written),
       count(*) FILTER (WHERE NOT s.written AND s.row_data <> s.kept_row),
       count(*) FILTER (WHERE s.unfit)
  FROM staged s
"""

#: Two new twins for each transfer the copy holds none of (one created after
#: the upgrade, or one the upgrade met holding none), by :data:`_BUILT_SQL`:
#: new ids, version 1, created when the transfer was, updated now.  ``built``
#: draws each id ONCE per twin, in transfer-then-side order: the draw is a
#: select-list call, which PostgreSQL evaluates once per row and after the
#: sort, and the row is expanded below by ONE ``jsonb_populate_record`` call
#: per twin, a LATERAL function scan.  The expansion this replaced,
#: ``(f(x)).*`` with the draw inside ``f``'s argument, called ``f`` once per
#: column and drew 25 ids per twin.  ``MATERIALIZED`` states what PostgreSQL
#: does anyway, since it never inlines a CTE that calls a volatile
#: function.
_BUILD_NEW_SQL = f"""
WITH built AS MATERIALIZED (
    SELECT b.twin, b.created_at,
           nextval(pg_get_serial_sequence('budget.transactions', 'id')) AS id
      FROM ({_BUILT_SQL}) b
     WHERE NOT EXISTS (
             SELECT 1 FROM system.transfer_twin_purge p
              WHERE p.transfer_id = b.transfer_id
           )
     ORDER BY b.transfer_id, b.is_income
)
INSERT INTO budget.transactions
SELECT r.*
  FROM built
  CROSS JOIN LATERAL jsonb_populate_record(
        NULL::budget.transactions,
        built.twin || jsonb_build_object(
          'id', built.id,
          'created_at', built.created_at,
          'updated_at', now(),
          'version_id', 1
        )
       ) r
"""


def refuse_twins_that_cannot_go(bind) -> None:
    """Refuse the upgrade while a twin could not be deleted and restored cleanly.

    Or while a transfer's twins would be built back under another user's
    category (the module docstring, item 1).  **Module-level so a test can
    DRIVE each refusal** (the chain's own pattern,
    ``e616adf7fe22.refuse_unlinkable_rows``).

    Args:
        bind: A SQLAlchemy connection.

    Raises:
        RuntimeError: Naming each offending twin or transfer and the query that
            finds it.  Nothing has been written.
    """
    referenced_sql = referenced_twins_sql(bind)
    referenced = [tuple(row) for row in bind.execute(sa.text(referenced_sql))]
    unpaired = [tuple(row) for row in bind.execute(sa.text(_UNPAIRED_SQL))]
    drifted = [tuple(row) for row in bind.execute(sa.text(_MIRROR_DRIFT_SQL))]
    foreign = [
        tuple(row) for row in bind.execute(sa.text(_FOREIGN_CATEGORY_SQL))
    ]
    foreign_transfers = [
        tuple(row)
        for row in bind.execute(sa.text(_FOREIGN_TRANSFER_CATEGORY_SQL))
    ]
    problems = []
    if referenced:
        problems.append(
            f"{len(referenced)} twin reference(s) would be nulled, cascaded "
            f"or refused by the delete (twin id, transfer id, what names it; "
            f"first {_NAMED_AT_MOST}: {referenced[:_NAMED_AT_MOST]}; diagnose "
            f"with: {referenced_sql})"
        )
    if unpaired:
        problems.append(
            f"{len(unpaired)} transfer(s) hold other than one expense and one "
            f"income twin (transfer id, expense twins, income twins; first "
            f"{_NAMED_AT_MOST}: {unpaired[:_NAMED_AT_MOST]}; diagnose with: "
            f"{_UNPAIRED_SQL.strip()})"
        )
    if drifted:
        problems.append(
            f"{len(drifted)} twin(s) are not a copy of their transfer: a "
            f"copied field differs (transfer id, twin id, the fields; first "
            f"{_NAMED_AT_MOST}: {drifted[:_NAMED_AT_MOST]}; diagnose with: "
            f"{_MIRROR_DRIFT_SQL.strip()})"
        )
    if foreign:
        problems.append(
            f"{len(foreign)} twin(s) are filed under a category that is not "
            f"their owner's (transfer id, twin id, category id, that "
            f"category's owner id, the transfer's own category id, THAT "
            f"category's owner id: where the transfer names a category whose "
            f"owner is not the transfer's, the transfer is refused for its own "
            f"category as well and is a row to repair, its twins with it; "
            f"otherwise the twin alone is; "
            f"first {_NAMED_AT_MOST}: {foreign[:_NAMED_AT_MOST]}; diagnose "
            f"with: {_FOREIGN_CATEGORY_SQL.strip()})"
        )
    if foreign_transfers:
        problems.append(
            f"{len(foreign_transfers)} transfer(s) are filed under a category "
            f"that is not their owner's, under which the downgrade would build "
            f"their twins (transfer id, category id, that category's owner "
            f"id, the transfer's owner id; first {_NAMED_AT_MOST}: "
            f"{foreign_transfers[:_NAMED_AT_MOST]}; diagnose with: "
            f"{_FOREIGN_TRANSFER_CATEGORY_SQL.strip()})"
        )
    if problems:
        raise RuntimeError(
            "X-bi-6-4d-2 refuses: " + "; ".join(problems) + ".  Nothing was "
            "written; each is the developer's to rule (rulings R-BAL166, "
            "R-BAL260)."
        )


def upgrade():
    """Keep a copy of every transfer twin, delete them, and refuse a new one."""
    bind = op.get_bind()
    refuse_twins_that_cannot_go(bind)
    op.execute(_CREATE_COPY_SQL)
    op.execute(_COMMENT_COPY_SQL)
    kept = bind.execute(sa.text(_KEEP_COPY_SQL)).rowcount
    deleted = bind.execute(sa.text(_DELETE_TWINS_SQL)).rowcount
    if deleted != kept:
        raise RuntimeError(
            f"X-bi-6-4d-2 kept {kept} twin(s) and deleted {deleted}; the "
            "transaction rolls back."
        )
    op.create_check_constraint(
        "ck_transactions_names_no_transfer", "transactions",
        "transfer_id IS NULL", schema="budget",
    )
    print(
        f"X-bi-6-4d-2: {deleted} transfer twin row(s) kept in "
        "system.transfer_twin_purge and deleted; 0 refused.  A transaction "
        "naming a transfer is now refused (ck_transactions_names_no_transfer)."
    )


def downgrade():
    """Restore every kept twin, build the missing ones, drop the copy."""
    bind = op.get_bind()
    copy_absent = bind.execute(sa.text(_COPY_ABSENT_SQL)).scalar_one()
    if copy_absent:
        op.execute(_CREATE_COPY_SQL)
    op.drop_constraint(
        "ck_transactions_names_no_transfer", "transactions",
        schema="budget", type_="check",
    )
    restored, rebuilt, changed, unfit = bind.execute(
        sa.text(_RESTORE_KEPT_SQL),
    ).one()
    built = bind.execute(sa.text(_BUILD_NEW_SQL)).rowcount
    op.execute("DROP TABLE system.transfer_twin_purge")
    print(
        f"X-bi-6-4d-2 downgrade: {restored} kept twin(s) restored: {rebuilt} "
        f"in line with a transfer written since; {changed} of an unwritten "
        "transfer differ from their kept row, saying what the transfer and "
        f"its side's record say now ({unfit} of them because a kept value no "
        "longer fits, ruling R-BAL261); the rest exactly as kept.  "
        f"{built} built new for transfers holding none"
        + (" (no copy table was found: none restored, every twin built new)."
           if copy_absent else ".")
    )
