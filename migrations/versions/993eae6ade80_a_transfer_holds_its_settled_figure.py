"""a transfer holds its settled figure: the figure and who wrote it, once, on the transfer

Revision ID: 993eae6ade80
Revises: 82f7b7ed4332
Create Date: 2026-10-24
Review: developer, 2026-10-22 (ruling R-BAL365: a settled transfer stores its
figure and who wrote it, 0.00 for a close of nothing; ruling R-BAL366: the
figure lives on the transfer and a side's record holds none)

Plan step **balance:X-bi-6-4d-2** of ``docs/audits/balance_architecture/README.md``
(its 4d-4 half, first commit).  **A settled transfer's figure had two homes**:
each side's payment record carried ``amount`` and ``figure_source_id``, and the
one writer (the status seam's Transfer arm) wrote the same pair onto both.  The
transfer's money is ONE figure, so this revision gives it one home on the
transfer, ``budget.transfers.settled_figure`` and ``settled_figure_source_id``,
and every reader reads it there:

  1. **It refuses, writing nothing, while a stored transfer's records do not
     state one figure** (:func:`refuse_unfigured_transfers`), each named with
     its class and the query that finds it: a record on ONE side only; two
     records whose figure or source differ; a SETTLED transfer holding no
     record; a settled transfer holding an un-dated record.  The last class is
     the band rule's (``e616adf7fe22``), which refuses it at every commit, and
     is re-counted here because the copy below would carry it.  A settled
     transfer holding no record is a ``$0.00`` close from before this revision,
     which stores no figure source (rulings **R-BAL82**, **R-BAL141**): this
     revision writes NO guess for one (who typed the zero cannot be read back),
     and its repair is in the refusal's words.  Measured on production before
     the build: every class empty (the 4d-4 design record
     ``o2_zero_close_count_PRODUCTION.md``); the release rehearsal re-counts.
  2. It adds the two columns, the source's key onto
     ``ref.movement_figure_sources`` (``RESTRICT``) and two CHECKs: the figure
     is never negative (``$0.00`` stays storable until plan step
     ``balance:X-db``, ruling R-BAL365), and the pair is stored whole or not
     at all.
  3. It copies each transfer's figure and source off its from-side record --
     DATED under a settled transfer, UN-dated under one a revert kept them for
     (ruling **R-BAL61**) -- and checks every transfer holding records got one.

**The records keep their copy in this revision**: the next revision of the same
step empties them and makes a side record holding a figure unstorable.  That
transitional second copy lives only inside the held branch, which never ships
without the next revision.

The downgrade drops the columns.  A transfer holding records keeps its figure
and who wrote it on those records -- unless a figure stored on a transfer
disagrees with its records, which no door writes and the downgrade refuses,
named, rather than discarding the figure the app was reading.  A ``$0.00``
close stored since the upgrade holds no record, so the downgrade LOSES who
wrote its ``0.00``: nothing is left to carry it, and the transfer becomes the
close of nothing it was before this revision, which stores no figure source.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "993eae6ade80"
down_revision = "82f7b7ed4332"
branch_labels = None
depends_on = None


#: Every transfer whose side records do not state ONE figure, with its class.
#: At most one record per side (``uq_transaction_entries_one_expense_side_record``
#: / ``..._one_income_side_record``), so the two outer joins yield one row per
#: transfer.
_UNFIGURED_SQL = """
WITH sides AS (
    SELECT x.id, s.is_settled,
           f.id AS f_id, f.amount AS f_amount,
           f.figure_source_id AS f_source, f.settled_on AS f_day,
           t.id AS t_id, t.amount AS t_amount,
           t.figure_source_id AS t_source, t.settled_on AS t_day
    FROM budget.transfers x
    JOIN ref.statuses s ON s.id = x.status_id
    LEFT JOIN budget.transaction_entries f ON f.expense_transfer_id = x.id
    LEFT JOIN budget.transaction_entries t ON t.income_transfer_id = x.id
), classed AS (
    SELECT id, CASE
        WHEN (f_id IS NULL) <> (t_id IS NULL) THEN 'a record on one side only'
        WHEN f_id IS NOT NULL
             AND (f_amount <> t_amount OR f_source <> t_source)
            THEN 'two records stating different figures'
        WHEN is_settled AND f_id IS NULL THEN 'settled holding no record'
        WHEN is_settled AND (f_day IS NULL OR t_day IS NULL)
            THEN 'settled holding an un-dated record'
    END AS class
    FROM sides
)
SELECT class, count(*), array_agg(id ORDER BY id)
FROM classed WHERE class IS NOT NULL
GROUP BY class ORDER BY class
"""

#: The copy: each transfer holding records takes its from-side record's pair.
#: :func:`refuse_unfigured_transfers` has refused every transfer whose two
#: records disagree or whose records are one-sided, so the from-side's pair IS
#: the transfer's.
_COPY_SQL = """
UPDATE budget.transfers x
SET settled_figure = f.amount,
    settled_figure_source_id = f.figure_source_id
FROM budget.transaction_entries f
WHERE f.expense_transfer_id = x.id
"""

#: ``(transfers holding records, of them holding a figure)``.
_COPIED_SQL = """
SELECT count(*), count(x.settled_figure)
FROM budget.transfers x
WHERE EXISTS (
    SELECT 1 FROM budget.transaction_entries e WHERE e.expense_transfer_id = x.id
)
"""

#: Every transfer whose stored figure the records it holds do not state, or
#: that stores a figure while holding no record other than a settled ``0.00``
#: (a close of nothing, which holds none): the shapes the downgrade would lose.
_DRIFTED_SQL = """
SELECT x.id
FROM budget.transfers x
JOIN ref.statuses s ON s.id = x.status_id
LEFT JOIN budget.transaction_entries f ON f.expense_transfer_id = x.id
LEFT JOIN budget.transaction_entries t ON t.income_transfer_id = x.id
WHERE (f.id IS NOT NULL AND (
          x.settled_figure IS DISTINCT FROM f.amount
          OR x.settled_figure_source_id IS DISTINCT FROM f.figure_source_id))
   OR (t.id IS NOT NULL AND (
          x.settled_figure IS DISTINCT FROM t.amount
          OR x.settled_figure_source_id IS DISTINCT FROM t.figure_source_id))
   OR (f.id IS NULL AND t.id IS NULL AND x.settled_figure IS NOT NULL
       AND NOT (s.is_settled AND x.settled_figure = 0))
ORDER BY x.id
"""

#: The repair the refusal names for a ``$0.00`` close stored before this
#: revision, the one class a door wrote.
_ZERO_CLOSE_REPAIR = (
    "a settled transfer holding no record is a $0.00 close, which stores no "
    "figure source before this revision: revert each to Projected in the "
    "app BEFORE upgrading, upgrade, then settle it again at $0.00 so the "
    "figure and who typed it are stored"
)


def refuse_unfigured_transfers(bind) -> None:
    """Refuse the upgrade while a stored transfer's records do not state one figure.

    Asked before any write, so a refusal keeps nothing.  Module-level so a
    test can DRIVE it, as ``e616adf7fe22``'s census functions are.

    Args:
        bind: A SQLAlchemy connection.

    Raises:
        RuntimeError: Naming each class, its count and its transfers, the
            query that finds them, and the repair for the class a door wrote.
    """
    found = [tuple(row) for row in bind.execute(sa.text(_UNFIGURED_SQL))]
    if found:
        raise RuntimeError(
            "X-bi-6-4d-2 refuses: transfer(s) whose payment records do not "
            "state ONE settled figure, by (class, count, transfer ids): "
            f"{found}.  Diagnose with: {_UNFIGURED_SQL.strip()}.  "
            f"Repair: {_ZERO_CLOSE_REPAIR}; every other class is written by no "
            "door and is the developer's to rule.  Nothing was written: the "
            "revision rolls back whole."
        )


def refuse_drifted_figures(bind) -> None:
    """Refuse the downgrade while a transfer's stored figure is not its records'.

    The downgrade drops the transfer's figure and leaves the records' copy as
    the only one, so a transfer whose figure the records do not state -- or a
    figure stored with no record other than a settled ``$0.00`` close's --
    would lose the figure the app was reading.  No door writes either shape.

    Args:
        bind: A SQLAlchemy connection.

    Raises:
        RuntimeError: Naming each transfer and the query that finds it.
    """
    drifted = [row[0] for row in bind.execute(sa.text(_DRIFTED_SQL))]
    if drifted:
        raise RuntimeError(
            f"X-bi-6-4d-2's downgrade refuses: {len(drifted)} transfer(s) store "
            f"a settled figure their records do not state ({drifted}; diagnose "
            f"with: {_DRIFTED_SQL.strip()}).  Dropping the column would discard "
            "the figure the app was reading.  Nothing was written."
        )


def upgrade():
    """Give a settled transfer's figure and its source ONE home, on the transfer."""
    bind = op.get_bind()
    refuse_unfigured_transfers(bind)
    op.add_column(
        "transfers",
        sa.Column("settled_figure", sa.Numeric(12, 2), nullable=True),
        schema="budget",
    )
    op.add_column(
        "transfers",
        sa.Column("settled_figure_source_id", sa.Integer(), nullable=True),
        schema="budget",
    )
    op.create_foreign_key(
        "fk_transfers_settled_figure_source_id", "transfers",
        "movement_figure_sources", ["settled_figure_source_id"], ["id"],
        source_schema="budget", referent_schema="ref", ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_transfers_settled_figure_non_negative", "transfers",
        "settled_figure >= 0", schema="budget",
    )
    op.create_check_constraint(
        "ck_transfers_settled_figure_source_pairing", "transfers",
        "(settled_figure IS NULL) = (settled_figure_source_id IS NULL)",
        schema="budget",
    )
    op.execute(_COPY_SQL)
    holding, copied = bind.execute(sa.text(_COPIED_SQL)).one()
    if holding != copied:
        raise RuntimeError(
            f"X-bi-6-4d-2's copy stored a figure on {copied} of the {holding} "
            "transfer(s) holding payment records; the transaction rolls back."
        )
    print(
        f"X-bi-6-4d-2: {copied} transfer(s) holding payment records now store "
        "their settled figure and who wrote it; 0 refused."
    )


def downgrade():
    """Drop the transfer's settled figure; its records' copy is the only one again."""
    refuse_drifted_figures(op.get_bind())
    op.drop_constraint(
        "ck_transfers_settled_figure_source_pairing", "transfers",
        schema="budget", type_="check",
    )
    op.drop_constraint(
        "ck_transfers_settled_figure_non_negative", "transfers",
        schema="budget", type_="check",
    )
    op.drop_constraint(
        "fk_transfers_settled_figure_source_id", "transfers",
        schema="budget", type_="foreignkey",
    )
    op.drop_column("transfers", "settled_figure_source_id", schema="budget")
    op.drop_column("transfers", "settled_figure", schema="budget")
