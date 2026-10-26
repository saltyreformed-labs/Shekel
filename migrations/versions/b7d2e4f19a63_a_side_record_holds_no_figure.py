"""a side record holds no figure: a transfer's figure is stored once, on the transfer

Revision ID: b7d2e4f19a63
Revises: 993eae6ade80
Create Date: 2026-10-24
Review: developer, 2026-10-22 (ruling R-BAL366: one movement table, and a
transfer side's record holds no amount and no figure source while every other
movement holds both, by database CHECKs; destructive: two columns emptied on
every side record and their NOT NULL dropped)

Plan step **balance:X-bi-6-4d-2** of ``docs/audits/balance_architecture/README.md``
(its 4d-4 half, second commit).  Revision ``993eae6ade80`` gave a settled
transfer's figure and who wrote it a home on the transfer
(``budget.transfers.settled_figure`` / ``settled_figure_source_id``) and every
reader reads it there; each side's payment record still carried a copy.  This
revision deletes the copy, so the figure has ONE home (rulings **R-BAL365**,
**R-BAL366**):

  1. **It refuses, writing nothing, while a stored transfer breaks the rule it
     installs** (:func:`refuse_unruled_transfers`), each named with its class
     and the query that finds it: a record stating a figure its transfer does
     not store (emptying it would discard the figure); a settled transfer
     storing no figure; a transfer not settled storing ``0.00``; a figure its
     records do not match (above zero needs a record on each side, none or
     ``0.00`` needs none).  The figure arm grades what a transaction leaves,
     never a row already stored, so this census is the only grade a stored
     row gets.  No door writes any class (the Transfer arm writes the figure
     and the records in one act).
  2. It drops NOT NULL on ``transaction_entries.amount`` and
     ``figure_source_id`` and empties both on every side record.
  3. It adds ``ck_transaction_entries_side_record_states_no_figure`` and
     ``ck_transaction_entries_side_record_names_no_figure_source``: a side
     record's amount and source are EMPTY, every other movement's are
     filled.  ``ck_transaction_entries_positive_amount`` stays (a NULL
     passes it).
  4. It installs the band's figure arm (:mod:`app.side_band_infrastructure`,
     literal arms ``(BAND_ARM, FIGURE_ARM)``): at commit, a figure above zero
     is recorded on both sides, no figure or ``0.00`` on neither; a settled
     transfer states its figure; a transfer not settled never holds ``0.00``
     (ruling R-BAL365's "a revert of a $0.00 close clears it", while a revert
     that keeps the records keeps the figure they moved: ruling **R-BAL61**,
     X-au-c3's "a revert is non-destructive").

The downgrade re-installs the band alone (``(BAND_ARM,)``), drops the two
CHECKs, copies each transfer's figure and source back onto each of its records
and sets NOT NULL back, refusing with the diagnostic query if a record would
stay empty.  A settled ``0.00`` transfer holds no record, so nothing is copied
for it.
"""
from alembic import op
import sqlalchemy as sa

from app.side_band_infrastructure import (
    BAND_ARM,
    FIGURE_ARM,
    apply_side_band_infrastructure,
)


# revision identifiers, used by Alembic.
revision = "b7d2e4f19a63"
down_revision = "993eae6ade80"
branch_labels = None
depends_on = None


#: Every transfer the figure arm would refuse, or whose records state a
#: figure it does not store, with its class.  At most one record per side
#: (``uq_transaction_entries_one_*_side_record``), so ``records`` is 0, 1 or 2.
_UNRULED_SQL = """
WITH sides AS (
    SELECT x.id, s.is_settled, x.settled_figure AS figure,
           count(e.id) AS records,
           count(e.id) FILTER (
               WHERE e.amount IS DISTINCT FROM x.settled_figure
                  OR e.figure_source_id IS DISTINCT FROM x.settled_figure_source_id
           ) AS disagreeing
    FROM budget.transfers x
    JOIN ref.statuses s ON s.id = x.status_id
    LEFT JOIN budget.transaction_entries e
        ON e.expense_transfer_id = x.id OR e.income_transfer_id = x.id
    GROUP BY x.id, s.is_settled
), classed AS (
    SELECT id, CASE
        WHEN disagreeing > 0
            THEN 'a record stating a figure its transfer does not store'
        WHEN is_settled AND figure IS NULL THEN 'settled storing no figure'
        WHEN NOT is_settled AND figure = 0 THEN 'not settled storing 0.00'
        WHEN records <> CASE WHEN coalesce(figure > 0, false) THEN 2 ELSE 0 END
            THEN 'a figure its records do not match'
    END AS class
    FROM sides
)
SELECT class, count(*), array_agg(id ORDER BY id)
FROM classed WHERE class IS NOT NULL
GROUP BY class ORDER BY class
"""

#: A transfer side's record: the rows this revision empties.
_SIDE_RECORD = "(expense_transfer_id IS NOT NULL OR income_transfer_id IS NOT NULL)"

#: The copy back: each side record takes its transfer's figure and source.
_COPY_BACK_SQL = """
UPDATE budget.transaction_entries e
SET amount = x.settled_figure,
    figure_source_id = x.settled_figure_source_id
FROM budget.transfers x
WHERE e.expense_transfer_id = x.id OR e.income_transfer_id = x.id
"""

#: Every movement the copy back left empty: the NOT NULL the downgrade sets
#: back would refuse each.
_EMPTY_SQL = """
SELECT id, expense_transfer_id, income_transfer_id
FROM budget.transaction_entries
WHERE amount IS NULL OR figure_source_id IS NULL
ORDER BY id
"""

#: The two CHECKs, ``(name, condition)``.
_CHECKS = (
    (
        "ck_transaction_entries_side_record_states_no_figure",
        "(expense_transfer_id IS NULL AND income_transfer_id IS NULL) "
        "= (amount IS NOT NULL)",
    ),
    (
        "ck_transaction_entries_side_record_names_no_figure_source",
        "(expense_transfer_id IS NULL AND income_transfer_id IS NULL) "
        "= (figure_source_id IS NOT NULL)",
    ),
)


def refuse_unruled_transfers(bind) -> None:
    """Refuse the upgrade while a stored transfer breaks the figure arm or its records disagree.

    Asked before any write, so a refusal keeps nothing.  Module-level so a
    test can DRIVE it, as ``993eae6ade80``'s census function is.

    Args:
        bind: A SQLAlchemy connection.

    Raises:
        RuntimeError: Naming each class, its count and its transfers, and the
            query that finds them.
    """
    found = [tuple(row) for row in bind.execute(sa.text(_UNRULED_SQL))]
    if found:
        raise RuntimeError(
            "X-bi-6-4d-2 refuses: transfer(s) whose settled figure, status "
            "and payment records the figure rule would refuse, by (class, "
            f"count, transfer ids): {found}.  Diagnose with: "
            f"{_UNRULED_SQL.strip()}.  No door writes any class; each is the "
            "developer's to rule.  Nothing was written: the revision rolls "
            "back whole."
        )


def _set_figure_columns_nullable(nullable: bool) -> None:
    """Set NOT NULL on, or drop it from, the entries' ``amount`` and ``figure_source_id``."""
    op.alter_column(
        "transaction_entries", "amount",
        existing_type=sa.Numeric(12, 2), nullable=nullable, schema="budget",
    )
    op.alter_column(
        "transaction_entries", "figure_source_id",
        existing_type=sa.Integer(), nullable=nullable, schema="budget",
    )


def upgrade():
    """Empty every side record's figure and source; the transfer's is the one home."""
    bind = op.get_bind()
    refuse_unruled_transfers(bind)
    _set_figure_columns_nullable(True)
    emptied = bind.execute(sa.text(
        "UPDATE budget.transaction_entries "
        "SET amount = NULL, figure_source_id = NULL "
        f"WHERE {_SIDE_RECORD}"
    )).rowcount
    for name, condition in _CHECKS:
        op.create_check_constraint(
            name, "transaction_entries", condition, schema="budget",
        )
    apply_side_band_infrastructure(op.execute, arms=(BAND_ARM, FIGURE_ARM))
    print(
        f"X-bi-6-4d-2: {emptied} transfer side record(s) emptied of their "
        "figure; each transfer's settled figure is the one home; 0 refused."
    )


def downgrade():
    """Copy each transfer's figure back onto its records; the band alone again."""
    apply_side_band_infrastructure(op.execute, arms=(BAND_ARM,))
    for name, _condition in reversed(_CHECKS):
        op.drop_constraint(
            name, "transaction_entries", schema="budget", type_="check",
        )
    bind = op.get_bind()
    bind.execute(sa.text(_COPY_BACK_SQL))
    empty = [tuple(row) for row in bind.execute(sa.text(_EMPTY_SQL))]
    if empty:
        raise RuntimeError(
            f"X-bi-6-4d-2's downgrade refuses: {len(empty)} movement(s) would "
            f"stay without a figure or its source under NOT NULL, as (id, "
            f"expense_transfer_id, income_transfer_id): {empty}.  Diagnose "
            f"with: {_EMPTY_SQL.strip()}.  A side record whose transfer stores "
            "no figure is a state the figure rule refuses at commit.  The "
            "transaction rolls back."
        )
    _set_figure_columns_nullable(False)
