"""purge the phantom loan payments generated before their loan originated

The data half of plan step C9a.  ``a1c7e2f4b930`` stops the recurrence engine
from GENERATING a payment before its loan exists; this removes the rows the
missing bound already produced.

**What these rows are.** ``create_payment_transfer`` (and the generic
``POST /transfers`` path) built a rule with no start bound and generated an
installment into every materialized pay period, including those preceding the
loan's origination.  Measured on a mortgage closing 2026-04-15: three of four
generated payments were pre-origination.  Each one debits the cash projection
for a loan that does not exist yet, and each becomes a money erasure the moment
it settles -- the fold splits a payment whose installment falls at or before
origination against a ZERO balance, booking $0.00 principal and routing the whole
payment to a Refund Receivable, after which the origination anchor resets the
balance over it.

**Scope -- the same safety predicate the recurrence engine itself applies**
(``_recurrence_common.partition_regeneration_rows``), so this deletes only what a
routine regeneration would already delete:

* the row is template-linked (AUTO-GENERATED, not hand-created);
* its status is NOT immutable -- a settled payment is never touched (see below);
* it is not ``is_override`` (hand-edited) and not soft-deleted;
* and its installment falls at or before the loan's ``origination_date``.

The installment is ``COALESCE(due_date, monthly_due_date(period start,
payment_day))`` -- the same derivation ``loan_loaders.loan_payment_due_date``
applies, so this selects exactly the rows the fold would erase, not "rows that
look old".

**A SETTLED pre-origination payment is deliberately left in place and reported.**
Its cash really did leave the funding account, so deleting it would silently
hand the money back; and it carries posting-ledger entries, which raw SQL must
not strand (only settled transfers post -- verified on real data: 151 projected
transfers, zero journal entries; 14 paid, 16 entries).  Such a row is an
F1-class data correction needing a human decision (was it a down payment? should
it reduce the opening principal?), so ``upgrade`` prints it loudly rather than
guessing.  There are none on production data.

Verified against the dev clone before writing: **zero** rows match on either
count -- both real loans originated (2018-12-01, 2023-02-14) long before any
materialized pay period.  This revision is therefore a no-op there and exists
for any database that carries the shape.

Destructive (deletes ``budget.transfers`` rows; their shadow
``budget.transactions`` follow by ``ON DELETE CASCADE``).  Every deleted row --
parent and both shadows -- is snapshotted whole into
``system.pre_origination_purge`` as ``jsonb``, so ``downgrade`` restores the
exact pre-migration state rather than re-deriving it.

**A missing snapshot is read as an empty one** (finding BAL-627, card
plan#1778): the downgrade restores nothing and finishes.  A database built at
head by ``scripts/init_database.py`` (``create_all``, then a stamp) never ran
this upgrade and holds no snapshot table, and the downgrade died there with
``UndefinedTable`` -- the shape ``82f7b7ed4332``'s downgrade already reads this
way.  It cannot tell that database from one that ran the upgrade and lost the
table since, so its line says only that no snapshot table was found.

Review: developer, 2026-07-19 (ruling: "C9 should clean up the problem that it
causes").

Revision ID: b2d8f3a6c541
Revises: a1c7e2f4b930
Create Date: 2026-07-19 23:58:00.000000
"""
import calendar
import datetime

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic.
revision = 'b2d8f3a6c541'
down_revision = 'a1c7e2f4b930'
branch_labels = None
depends_on = None


# Every template-linked transfer INTO a configured loan, with the three inputs
# the installment derivation needs (stored due date, the pay period's start as
# the fallback basis, and the loan's payment day) plus the loan's origination
# date and the status flag that protects a settled row.  The ``loan_params``
# join IS the "is this a loan payment?" predicate -- the same one
# ``loan_recurrence_sync.bind_rule_to_loan`` applies.
_CANDIDATES = sa.text("""
    SELECT t.id AS transfer_id,
           t.due_date,
           pp.start_date AS period_start,
           lp.payment_day,
           lp.origination_date,
           s.is_immutable,
           t.is_override,
           t.is_deleted,
           a.name AS loan_name,
           t.amount
    FROM budget.transfers t
    JOIN budget.loan_params lp ON lp.account_id = t.to_account_id
    JOIN budget.accounts a ON a.id = t.to_account_id
    JOIN budget.pay_periods pp ON pp.id = t.pay_period_id
    JOIN ref.statuses s ON s.id = t.status_id
    WHERE t.transfer_template_id IS NOT NULL
""")

_SNAPSHOT_TRANSFER = sa.text("""
    INSERT INTO system.pre_origination_purge (kind, row_id, row_data)
    SELECT 'transfer', x.id, to_jsonb(x)
    FROM budget.transfers x WHERE x.id = :transfer_id
    ON CONFLICT (kind, row_id) DO NOTHING
""")
_SNAPSHOT_SHADOWS = sa.text("""
    INSERT INTO system.pre_origination_purge (kind, row_id, row_data)
    SELECT 'transaction', x.id, to_jsonb(x)
    FROM budget.transactions x WHERE x.transfer_id = :transfer_id
    ON CONFLICT (kind, row_id) DO NOTHING
""")
_DELETE_TRANSFER = sa.text(
    "DELETE FROM budget.transfers WHERE id = :transfer_id"
)

# Whether the snapshot is missing: a database built at head by
# ``scripts/init_database.py`` never ran the upgrade that creates it.
_SNAPSHOT_ABSENT = sa.text(
    "SELECT to_regclass('system.pre_origination_purge') IS NULL"
)


def _monthly_due_date(reference, payment_day):
    """Return the first ``payment_day`` on or after ``reference``.

    A migration-local copy of
    ``app.services.rate_period_engine.monthly_due_date`` (a migration must not
    import app code -- the same rule ``c4e91a7b2d38`` follows).  This is the
    fallback ``loan_loaders.loan_payment_due_date`` applies when a loan payment
    carries no stored ``due_date``: the payment's pay period was chosen to
    contain its due date, so the first ``payment_day`` at or after the period
    start IS that due date.  ``payment_day`` is clamped to the month's length.

    Args:
        reference: The pay-period start date to search on or after.
        payment_day: The loan's contractual day-of-month due day, 1-31.

    Returns:
        The installment date this payment satisfies.
    """
    last_day = calendar.monthrange(reference.year, reference.month)[1]
    candidate = datetime.date(
        reference.year, reference.month, min(payment_day, last_day),
    )
    if candidate >= reference:
        return candidate
    year = reference.year + (1 if reference.month == 12 else 0)
    month = 1 if reference.month == 12 else reference.month + 1
    last_day = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, min(payment_day, last_day))


def _installment_of(row):
    """Return the installment a candidate row satisfies.

    ``COALESCE(due_date, monthly_due_date(period start, payment_day))`` -- the
    derivation ``loan_loaders.loan_payment_due_date`` applies, reproduced so the
    purge selects exactly the rows the fold erases.

    Args:
        row: A ``_CANDIDATES`` result mapping.

    Returns:
        The installment date.
    """
    if row["due_date"] is not None:
        return row["due_date"]
    return _monthly_due_date(row["period_start"], row["payment_day"])


def upgrade():
    """Delete the phantom pre-origination payments; report any settled one."""
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS system.pre_origination_purge (
            kind varchar(16) NOT NULL,
            row_id integer NOT NULL,
            row_data jsonb NOT NULL,
            PRIMARY KEY (kind, row_id)
        )
    """))
    bind = op.get_bind()
    purged = 0
    settled = []
    for row in bind.execute(_CANDIDATES).mappings().all():
        if _installment_of(row) > row["origination_date"]:
            continue
        if row["is_immutable"]:
            # Real money moved.  Never delete it here -- report it.
            settled.append(row)
            continue
        if row["is_override"] or row["is_deleted"]:
            # A hand-edited or already-removed row is a user decision, and the
            # recurrence engine treats both as regeneration CONFLICTS rather
            # than rows it may delete.  Leave them.
            continue
        params = {"transfer_id": row["transfer_id"]}
        bind.execute(_SNAPSHOT_TRANSFER, params)
        bind.execute(_SNAPSHOT_SHADOWS, params)
        bind.execute(_DELETE_TRANSFER, params)
        purged += 1

    print(f"Pre-origination loan payments purged: {purged}")
    for row in settled:
        print(
            "  WARNING: SETTLED pre-origination payment LEFT IN PLACE -- "
            f"transfer {row['transfer_id']} (${row['amount']}) into "
            f"'{row['loan_name']}', installment {_installment_of(row)} "
            f"vs origination {row['origination_date']}.  Its cash really "
            "moved and it carries posting-ledger entries, so it needs a "
            "manual decision (down payment vs mis-dated payment); the loan "
            "fold currently books it as $0.00 principal / a Refund Receivable."
        )


def downgrade():
    """Restore every transfer and shadow ``upgrade`` deleted.

    Re-inserts from the whole-row ``jsonb`` snapshot -- parents first so the
    shadows' ``transfer_id`` FK resolves -- then drops the snapshot table.  Rows
    the upgrade left alone were never snapshotted and are untouched.

    The restored rows keep their original ids, which is what makes the
    shadows' ``transfer_id`` and the transfers' own identity survive the round
    trip; the sequences are already past them, so no id is reissued.

    With no snapshot table there is nothing to restore and nothing to drop
    (the module docstring).
    """
    bind = op.get_bind()
    if bind.execute(_SNAPSHOT_ABSENT).scalar_one():
        print(
            "Pre-origination loan payments restored: 0 (no snapshot table "
            "was found)"
        )
        return
    for kind, table in (
        ("transfer", "budget.transfers"),
        ("transaction", "budget.transactions"),
    ):
        # Expand the whole-row snapshot back into the table it came from.  The
        # jsonb round-trips through ``json.dumps`` because psycopg2 decodes a
        # jsonb column to a dict, which must be re-serialised (and CAST) before
        # ``jsonb_populate_record`` can consume it.
        bind.execute(
            sa.text(
                f"INSERT INTO {table} "
                f"SELECT (jsonb_populate_record(NULL::{table}, p.row_data)).* "
                "FROM system.pre_origination_purge p "
                "WHERE p.kind = :kind "
                "ORDER BY p.row_id "
                "ON CONFLICT DO NOTHING"
            ),
            {"kind": kind},
        )
    op.execute(sa.text("DROP TABLE IF EXISTS system.pre_origination_purge"))
