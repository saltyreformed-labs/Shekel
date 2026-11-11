"""a loan states how its interest accrues: the convention list and its key on loan_params

Revision ID: 08fda2e0b18b
Revises: 5f2c46a35a79
Create Date: 2026-11-10

Plan step **recurrence:R16-d**, card plan#1245 (finding **D52**)::

    ref.interest_accrual_conventions               NEW, seeded (one row)
    budget.loan_params.interest_accrual_convention_id
                                                   NEW, NOT NULL, backfilled
    fk_loan_params_interest_accrual_convention     NEW, ON DELETE RESTRICT

**How a loan accrues interest is a fact of its NOTE**, and until this revision
it was a formula the app assumed: one twelfth of the annual rate per month,
the US fixed-rate mortgage's convention, hardcoded in
``app.utils.money.accrue_monthly_interest``.  It becomes a value on the loan,
a key into a list of conventions the one interest producer prices a window
under.

**The list has ONE row, ``monthly_1_12``** (developer ruling plan#3354): a
member joins with the first thing that uses it, so no loan can name a
convention the app cannot honour, and no guard is needed to refuse one.
``daily_actual_365`` arrives with the card's finance charge
(``credit_card:CC-3i``) or a loan's daily accrual (plan#3355); the savings
account's compounding rules join at plan#3356 (ruling plan#3348).

**No figure moves.**  Every existing loan is backfilled to ``monthly_1_12``,
the formula every walk already applies, so each loan's interest is what it
was to the cent.  Which convention each loan's note actually states is the
owner's to set when a second member exists (ruling plan#3345); the backfill
does not guess.

**NOT NULL in three steps** (the database rule for a populated table): the
column is added nullable, every row is set, a surviving NULL refuses the
revision naming the diagnostic SELECT, and only then is it locked NOT NULL
and keyed.  No ``server_default``: an FK id is not a static literal, so the
one door that writes a loan row (``routes/loan/params.create_params``) states
it.  ``RESTRICT`` because a ref row a loan names may never be deleted from
under it.

**Inline seed rationale.**  The row is seeded here so ``ref_cache.init()``
resolves ``InterestAccrualConventionEnum`` immediately after a bare ``flask db
upgrade``; ``app/ref_seeds.py`` carries the identical row for the idempotent
reseed (the dual-seed pattern ``tests/test_models/test_posting_ref_seed_parity.py``
guards).  ``ref.interest_accrual_conventions`` is a read-only catalogue and is
not audited, on the criteria that keep every other ref catalogue out;
``budget.loan_params`` is already audited, and its trigger records the new
column with the rest of the row.

Self-contained dependency policy: this migration imports nothing from
``app``.

**Reversible.**  The downgrade drops the key, the column and the table.  It
loses only the one fact this revision adds, which every loan holds at its one
possible value.
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '08fda2e0b18b'
down_revision = '5f2c46a35a79'
branch_labels = None
depends_on = None


_SEED_SQL = (
    "INSERT INTO ref.interest_accrual_conventions (name) VALUES "
    "('monthly_1_12') "
    "ON CONFLICT (name) DO NOTHING"
)

_BACKFILL_SQL = (
    "UPDATE budget.loan_params "
    "SET interest_accrual_convention_id = ("
    "SELECT id FROM ref.interest_accrual_conventions "
    "WHERE name = 'monthly_1_12')"
)

_DIAGNOSTIC_SQL = (
    "SELECT id, account_id FROM budget.loan_params "
    "WHERE interest_accrual_convention_id IS NULL"
)


def _refuse_a_surviving_null() -> None:
    """Refuse the revision while any loan row still lacks a convention.

    The backfill names one row of a table this revision just seeded, so a NULL
    can survive only if that seed failed to land; the count and the SELECT
    that lists the rows are the diagnostic, rather than the NOT NULL alter's
    opaque error.

    Raises:
        RuntimeError: When a ``budget.loan_params`` row holds a NULL
            ``interest_accrual_convention_id`` after the backfill.
    """
    remaining = op.get_bind().execute(
        sa.text(
            "SELECT COUNT(*) FROM budget.loan_params "
            "WHERE interest_accrual_convention_id IS NULL"
        )
    ).scalar()
    if remaining:
        raise RuntimeError(
            f"{remaining} budget.loan_params row(s) hold no interest accrual "
            f"convention after the backfill to 'monthly_1_12'.  Inspect with: "
            f"{_DIAGNOSTIC_SQL};"
        )


def upgrade():
    """Create the convention list, then give every loan row its key."""
    op.create_table(
        'interest_accrual_conventions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=30), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
        schema='ref',
    )
    op.execute(_SEED_SQL)

    op.add_column(
        'loan_params',
        sa.Column('interest_accrual_convention_id', sa.Integer(), nullable=True),
        schema='budget',
    )
    op.execute(_BACKFILL_SQL)
    _refuse_a_surviving_null()
    op.alter_column(
        'loan_params', 'interest_accrual_convention_id',
        nullable=False, schema='budget',
    )
    op.create_foreign_key(
        'fk_loan_params_interest_accrual_convention',
        'loan_params', 'interest_accrual_conventions',
        ['interest_accrual_convention_id'], ['id'],
        source_schema='budget', referent_schema='ref', ondelete='RESTRICT',
    )


def downgrade():
    """Drop the key, the column and the list, in reverse order."""
    op.drop_constraint(
        'fk_loan_params_interest_accrual_convention', 'loan_params',
        schema='budget', type_='foreignkey',
    )
    op.drop_column(
        'loan_params', 'interest_accrual_convention_id', schema='budget',
    )
    op.drop_table('interest_accrual_conventions', schema='ref')
