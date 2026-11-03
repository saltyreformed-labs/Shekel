"""A card's terms are dated: budget.credit_card_params gains effective_date

Plan#2331, developer ruling **plan#2333** (2026-11-02), as picked: "The
database's one-row-per-card rule becomes one row per card per date, as the
APR's is (picking this approves that change)."

Review: Josh, 2026-11-02 (plan#2333: the one-row-per-card key is replaced by
one row per card per date).

**What changes**::

    budget.credit_card_params
        + effective_date DATE NOT NULL   backfilled, then NOT NULL
        + ck_credit_card_params_effective_date_range
                                         2000-01-01 <= effective_date <= 2100-12-31
        - credit_card_params_account_id_key        UNIQUE (account_id)
        + uq_credit_card_params_account_effective_date
                                         UNIQUE (account_id, effective_date)

A card's terms were one row the terms form rewrote in place; every statement
and reward is DERIVED from them at read time (rulings **R-CC2**, **R-CC4**),
so a rewrite would have re-cut the past once a reader existed.  Each row is
now one VERSION of the whole term set, in force from its ``effective_date``
until the next row's -- the shape ``budget.rate_history`` gives the card's APR.

**The backfill, and why its date moves no figure.**  Each existing row becomes
its card's one version, dated the day it was first saved: ``created_at`` read
in the owner's display timezone (``America/New_York``,
``app.utils.dates.DISPLAY_TIMEZONE``, spelled here because a migration imports
nothing from ``app``).  The reader holds a card's EARLIEST version flat before
its date (``app.services.card_statement.terms_in_effect``), so a card with one
version derives the same terms on every day whatever its date is: the date is
the label the Card terms card shows, not an input any figure reads.  Nothing
derives a figure from the terms yet in any case (no ``app/`` module calls
``card_statement``), and production held no Credit Card account when this was
written (measured read-only 2026-11-02 11:30 UTC: 10 accounts, none revolving,
0 rows here).

**The date window** is the schema's (``app.utils.dates.CALENDAR_DATE_MIN`` /
``_MAX``) at the storage tier, as ``budget.template_amount_versions`` holds it:
the series holds flat backwards, so a four-digit-year typo would become a card's
earliest version.  It is added after the backfill, which reads ``created_at``,
a timestamp the application wrote, so every backfilled date is inside it.

The NOT NULL is the three-step form: add nullable, backfill, then verify no
row was missed before the constraint is set -- ``created_at`` is itself NOT
NULL, so a survivor means the table was changed outside the application.

**The downgrade** puts the one-row-per-card key back and drops the column.
That is only true to the old shape while every card holds ONE version, so it
REFUSES otherwise, naming each card and the SELECT that lists them: which
version a card should keep is the owner's to say (the old shape cannot hold
the others), and a downgrade that chose would delete terms the owner stated.

Self-contained dependency policy: this migration imports nothing from ``app``.

Revision ID: 0703995c8536
Revises: 858f569b2c17
Create Date: 2026-11-02
"""
from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '0703995c8536'
# The head of ``dev`` at ``bb637cafe``, measured on this branch after its merge
# (224 revisions, one head, by alembic.script.ScriptDirectory).  It was
# ``69634349bdce`` at authoring, until PR #641 landed ``858f569b2c17`` on it
# first and the merge queue refused this card's two heads.  Another lane
# landing a revision first makes two heads, which git merges cleanly and
# ``flask db upgrade`` refuses: re-point this at the measured head as the last
# edit before the pull request.
down_revision = '858f569b2c17'
branch_labels = None
depends_on = None


#: The table, stated once for every operation below.
_TABLE = 'credit_card_params'

#: The key this revision retires: PostgreSQL's generated name for the
#: ``unique=True`` the 1:1 mixin declared (``97f92340fffc``).
_ONE_PER_CARD = 'credit_card_params_account_id_key'

#: The key that replaces it, the model's ``CREDIT_CARD_TERMS_UNIQUE_CONSTRAINT``.
_ONE_PER_CARD_PER_DATE = 'uq_credit_card_params_account_effective_date'

#: The calendar window a stated date may fall in, the model's CHECK; literal
#: dates, because a migration imports nothing from ``app``.
_DATE_WINDOW = 'ck_credit_card_params_effective_date_range'
_DATE_WINDOW_SQL = (
    "effective_date BETWEEN DATE '2000-01-01' AND DATE '2100-12-31'"
)

#: The cards a downgrade cannot hold: more than one version each.
_CARDS_WITH_VERSIONS = (
    "SELECT account_id, COUNT(*) AS versions "
    "FROM budget.credit_card_params "
    "GROUP BY account_id HAVING COUNT(*) > 1 ORDER BY account_id"
)


def upgrade():
    """Date every terms row, then key the table one row per card per date."""
    op.add_column(
        _TABLE, sa.Column('effective_date', sa.Date(), nullable=True),
        schema='budget',
    )
    # The day each card's terms were first saved, in the owner's timezone.
    op.execute(
        "UPDATE budget.credit_card_params "
        "SET effective_date = (created_at AT TIME ZONE 'America/New_York')::date"
    )
    missed = op.get_bind().execute(sa.text(
        "SELECT COUNT(*) FROM budget.credit_card_params "
        "WHERE effective_date IS NULL"
    )).scalar()
    if missed:
        raise RuntimeError(
            f"{missed} budget.credit_card_params row(s) still have no "
            f"effective_date after the backfill from created_at, which is NOT "
            f"NULL -- the table was changed outside the application. List "
            f"them with: SELECT id, account_id, created_at FROM "
            f"budget.credit_card_params WHERE effective_date IS NULL"
        )
    op.alter_column(_TABLE, 'effective_date', nullable=False, schema='budget')
    op.create_check_constraint(
        _DATE_WINDOW, _TABLE, _DATE_WINDOW_SQL, schema='budget',
    )
    op.drop_constraint(_ONE_PER_CARD, _TABLE, schema='budget', type_='unique')
    op.create_unique_constraint(
        _ONE_PER_CARD_PER_DATE, _TABLE, ['account_id', 'effective_date'],
        schema='budget',
    )


def downgrade():
    """Restore one row per card, refusing while any card holds two versions."""
    cards = op.get_bind().execute(sa.text(_CARDS_WITH_VERSIONS)).all()
    if cards:
        named = ", ".join(
            f"account {row.account_id} ({row.versions} versions)"
            for row in cards
        )
        raise RuntimeError(
            f"Cannot restore one terms row per card: {named}. The old shape "
            f"holds one set of terms per card, and which version each card "
            f"keeps is the owner's to choose. List them with: "
            f"{_CARDS_WITH_VERSIONS}; delete all but the version to keep "
            f"(DELETE FROM budget.credit_card_params WHERE id IN (...)), then "
            f"run the downgrade again."
        )
    op.drop_constraint(
        _ONE_PER_CARD_PER_DATE, _TABLE, schema='budget', type_='unique',
    )
    op.create_unique_constraint(
        _ONE_PER_CARD, _TABLE, ['account_id'], schema='budget',
    )
    op.drop_constraint(_DATE_WINDOW, _TABLE, schema='budget', type_='check')
    op.drop_column(_TABLE, 'effective_date', schema='budget')
