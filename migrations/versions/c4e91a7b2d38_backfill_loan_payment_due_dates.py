"""backfill loan-payment due_dates to their true contractual due date

A loan payment's ``due_date`` is the installment it satisfies -- the loan's
``payment_day`` of some month.  Legacy loan-payment rows instead stored the
START of the pay period the recurrence generated them into: ``compute_due_date``
(:mod:`app.services.recurrence_engine`) falls back to ``period.start_date`` for
any rule carrying no ``day_of_month``, and nothing read the column back, so the
wrong value was harmless.

**It is no longer harmless: ``due_date`` is now a POSTING INPUT.**  The
amortization engine reads the stored value
(``loan_loaders.loan_payment_due_date``) -- it must, because a payment settled
LATE sits in a pay period that no longer contains its due date, and re-deriving
the due date from that period reports the NEXT month's installment (a July
payment shown as an August one, and a CONFIRMED schedule row stamped with a
FUTURE date, which breaks every date-basis balance walk that reads it).  The
genesis write walk
(``loan_posting_service._walk._merge_anchor_and_payment_events``) now orders
payments and applies the strict ``anchor_date < due_date`` post-anchor boundary
against it, so a wrong ``due_date`` changes which payments an anchor SUBSUMES
and therefore the POSTED balance.

Consequences for anyone changing this column in future:

* Any write to a loan payment's ``due_date`` MUST be followed by a posting
  reconcile (``loan_posting_service.sync_loan_postings_all_scenarios``), or the
  posted ledger and the resolver silently disagree.  ``transfer_service``
  enforces this by listing ``due_date`` in ``_POSTING_RELEVANT_FIELDS``.
* This migration's own rewrite is reconciled by the deploy pipeline:
  ``scripts/init_database.py`` runs ``upgrade(head)`` and THEN
  ``backfill_all_loan_postings()`` (reconcile-to-target, idempotent), so the
  ledger is re-derived from the corrected dates before a single request is
  served.  **A bare ``flask db upgrade`` does NOT run that hook** -- a local
  verification must call ``loan_posting_service.backfill_all_loan_postings()``
  directly afterwards (the same caveat ``f2a7c1e9b4d3`` carries).

Scope -- the LEGACY SIGNATURE, not "any date that looks odd".  A row is legacy
iff its stored ``due_date`` equals a pay-period START date for that user, which
is precisely what the fallback writer produced.  (It may be the start of a
DIFFERENT period than the row now sits in: a payment moved after generation
keeps the original period's start.)  Deliberately NOT keyed on "the day-of-month
differs from ``payment_day``": an operator may legitimately set a due date off
the contractual day (a servicer moves the date; a one-off arrangement), and
rewriting that to the next ``payment_day`` would silently move the payment to
the FOLLOWING month's installment.  Rows already correct are selected only when
their due date coincidentally falls on a period start, where the rule maps them
to themselves (a date already on ``payment_day`` is its own first
``payment_day`` on or after itself), so they are skipped unchanged.

All THREE rows of a transfer are rewritten together -- the parent
``budget.transfers`` and BOTH shadow ``budget.transactions``.  The parent is the
canonical value (Transfer Invariant 3; ``transfer_service.update_transfer``
mirrors it to both shadows), and the full-edit form pre-fills its input FROM the
parent -- so correcting only the income shadow would leave a stale parent that a
no-op "Save" would write straight back over the correction, re-posting the wrong
balance.

Verified against real production data -- 5 rewritten rows, and the rule recovers
each installment exactly:

    txn 1456  2026-03-26 (payment_day 1)  -> 2026-04-01
    txn 1458  2026-04-23 (payment_day 1)  -> 2026-05-01
    txn 1460  2026-05-21 (payment_day 1)  -> 2026-06-01
    txn 2162  2026-04-09 (payment_day 22) -> 2026-04-22
    txn 2164  2026-05-21 (payment_day 22) -> 2026-05-22

**A missing snapshot is read as an empty one** (finding BAL-627, card
plan#1778): the downgrade restores nothing and finishes.  A database built at
head by ``scripts/init_database.py`` (``create_all``, then a stamp) never ran
this upgrade and holds no snapshot table, and the downgrade died there with
``UndefinedTable`` -- the shape ``82f7b7ed4332``'s downgrade already reads this
way.  It cannot tell that database from one that ran the upgrade and lost the
table since, so its line says only that no snapshot table was found.

Review: developer, 2026-07-12.  Data-only (no business-table schema change); the
prior value of every row it rewrites -- transfer and both shadows -- is
snapshotted into ``system.loan_due_date_backfill`` so ``downgrade`` restores the
exact pre-migration state rather than re-deriving (and thereby corrupting
correctly-stored rows).

Revision ID: c4e91a7b2d38
Revises: ec6054b19620
Create Date: 2026-07-12 21:20:00.000000
"""
import calendar
import datetime

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic.
revision = 'c4e91a7b2d38'
down_revision = 'ec6054b19620'
branch_labels = None
depends_on = None


# The loan payments to correct, one row per TRANSFER: the INCOME shadow of a
# transfer into an account with LoanParams, whose stored due_date carries the
# legacy signature (it equals a pay-period start for that user).  Soft-deleted
# rows are included deliberately -- an undelete must not resurrect a wrong date.
#
# ``ref.transaction_types`` is resolved by NAME here rather than by a cached id.
# The app's id-over-name rule governs application code, which reads the ref
# cache; a migration has no cache and must run against whatever ids the target
# database assigned, so the stable natural key is the only correct join.
_LEGACY_LOAN_PAYMENTS = sa.text("""
    SELECT t.transfer_id, t.due_date, lp.payment_day
    FROM budget.transactions t
    JOIN budget.accounts a ON a.id = t.account_id
    JOIN budget.loan_params lp ON lp.account_id = t.account_id
    JOIN ref.transaction_types tt ON tt.id = t.transaction_type_id
    WHERE t.transfer_id IS NOT NULL
      AND tt.name = 'Income'
      AND t.due_date IS NOT NULL
      AND EXISTS (
          SELECT 1 FROM budget.pay_periods pp
          WHERE pp.user_id = a.user_id AND pp.start_date = t.due_date
      )
""")

_SNAPSHOT_TRANSFER = sa.text("""
    INSERT INTO system.loan_due_date_backfill (kind, row_id, old_due_date)
    SELECT 'transfer', x.id, x.due_date
    FROM budget.transfers x WHERE x.id = :transfer_id
    ON CONFLICT (kind, row_id) DO NOTHING
""")
_SNAPSHOT_SHADOWS = sa.text("""
    INSERT INTO system.loan_due_date_backfill (kind, row_id, old_due_date)
    SELECT 'transaction', x.id, x.due_date
    FROM budget.transactions x WHERE x.transfer_id = :transfer_id
    ON CONFLICT (kind, row_id) DO NOTHING
""")
_UPDATE_TRANSFER = sa.text(
    "UPDATE budget.transfers SET due_date = :due_date WHERE id = :transfer_id"
)
_UPDATE_SHADOWS = sa.text(
    "UPDATE budget.transactions SET due_date = :due_date "
    "WHERE transfer_id = :transfer_id"
)

# Whether the snapshot is missing: a database built at head by
# ``scripts/init_database.py`` never ran the upgrade that creates it.
_SNAPSHOT_ABSENT = sa.text(
    "SELECT to_regclass('system.loan_due_date_backfill') IS NULL"
)


def _monthly_due_date(reference, payment_day):
    """Return the first ``payment_day`` on or after ``reference``.

    A migration-local copy of
    ``app.services.rate_period_engine.monthly_due_date``: a migration must not
    import app code, which evolves independently of the schema this revision
    targets.  ``payment_day`` is clamped to the month's length, so a
    ``payment_day`` of 31 resolves to Feb 28/29.

    The legacy value is the start of the pay period that CONTAINED the true due
    date, so the first ``payment_day`` on or after it IS that due date.

    Args:
        reference: The date to search on or after.
        payment_day: The loan's contractual day-of-month due day, 1-31.

    Returns:
        The recovered contractual due date.
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


def upgrade():
    """Rewrite each legacy loan payment's due_date -- transfer AND both shadows."""
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS system.loan_due_date_backfill (
            kind varchar(16) NOT NULL,
            row_id integer NOT NULL,
            old_due_date date,
            PRIMARY KEY (kind, row_id)
        )
    """))
    bind = op.get_bind()
    for row in bind.execute(_LEGACY_LOAN_PAYMENTS).mappings().all():
        corrected = _monthly_due_date(row["due_date"], row["payment_day"])
        if corrected == row["due_date"]:
            # A correct due date that merely coincides with a pay-period start:
            # the rule maps it to itself.  Leave it (and its transfer) untouched.
            continue
        params = {"transfer_id": row["transfer_id"]}
        bind.execute(_SNAPSHOT_TRANSFER, params)
        bind.execute(_SNAPSHOT_SHADOWS, params)
        bind.execute(_UPDATE_TRANSFER, {**params, "due_date": corrected})
        bind.execute(_UPDATE_SHADOWS, {**params, "due_date": corrected})


def downgrade():
    """Restore the exact pre-migration due_date of every row upgrade rewrote.

    Reads the snapshot ``upgrade`` recorded -- parent transfers and both shadows
    alike -- so rows it never touched keep their values and rows it did touch
    return to their prior value, NULLs included.  Re-deriving from the pay period
    instead would corrupt correctly-stored modern rows, whose due date is not
    their period start.

    The caller must re-run ``loan_posting_service.backfill_all_loan_postings()``
    afterwards, exactly as the upgrade path does: the restored (legacy) due dates
    change the genesis walk's anchor boundary, so the posted ledger must be
    re-derived from them.

    With no snapshot table there is nothing to restore and nothing to drop
    (the module docstring).
    """
    bind = op.get_bind()
    if bind.execute(_SNAPSHOT_ABSENT).scalar_one():
        print(
            "Loan payment due dates restored: 0 (no snapshot table was found)"
        )
        return
    snapshot = bind.execute(sa.text("""
        SELECT kind, row_id, old_due_date FROM system.loan_due_date_backfill
    """)).mappings().all()
    for row in snapshot:
        table = (
            "budget.transfers" if row["kind"] == "transfer"
            else "budget.transactions"
        )
        bind.execute(
            sa.text(
                f"UPDATE {table} SET due_date = :due_date WHERE id = :row_id"
            ),
            {"due_date": row["old_due_date"], "row_id": row["row_id"]},
        )
    op.execute(sa.text("DROP TABLE IF EXISTS system.loan_due_date_backfill"))
