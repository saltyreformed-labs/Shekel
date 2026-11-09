"""a job learns its pay periods

Plan step **salary plan#3048** (task plan#3099, "A job learns its pay periods
from one stub's two dates"), ruling **plan#3096**::

    salary.salary_profiles    + period_end_days_before_payday
                                (integer, NOT NULL, CHECK 0..365, no default)

**What the column holds.**  How many days each of the job's pay periods ends
before the payday payroll SCHEDULED for it, the grid day before any holiday
moved it.  The job's form asks one stub's Pay Date and Period End and turns
them into this; ``pay_calendar.days_paid_for`` reads it for the days each
paycheck pays for.  Nothing priced reads it at this revision.

**The backfill is 6, for every existing job, and the derivation is one stub.**
Production holds one salary profile, the developer's.  His 2026-07-16 stub
prints Pay Date 2026-07-16 and Period End 2026-07-10, and 07-16 is a scheduled
Thursday no holiday moved, so its period ends 16 - 10 = 6 days before its
scheduled payday (the ruling's own words: "The update fills yours in from the
07-16 stub").  The column takes no server default: a job's gap is its
employer's fact, which no static value states for a job not yet made, so the
create form asks every new job for its two dates.

The three steps the rules require for NOT NULL on a populated table: the
column is added nullable, every row is set to 6, and a row still NULL is
refused by id before the column is made NOT NULL.  The CHECK comes last, over
filled rows.

The downgrade drops the CHECK and the column, losing each job's stated gap;
the older schema has no place for it and nothing at that revision reads it.
Additive DDL with a plain reversal: no ``Review:`` line is owed.

Revision ID: 0fe9c332b9b0
Revises: 6740ce13e174
Create Date: 2026-11-09
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0fe9c332b9b0"
down_revision = "6740ce13e174"
branch_labels = None
depends_on = None

_TABLE = "salary_profiles"
_SCHEMA = "salary"
_COLUMN = "period_end_days_before_payday"

#: The developer's gap, read off his 2026-07-16 stub (see the docstring).
_BACKFILL_DAYS = 6

#: The column's range, as ``app.models.salary_profile`` states it at this
#: revision (``PERIOD_END_DAYS_BEFORE_PAYDAY_MIN`` / ``_MAX``).  Literal here,
#: as every migration states its own DDL: a migration is history and must not
#: move when the model does.
_CHECK = "ck_salary_profiles_period_end_days_range"
_CHECK_PREDICATE = f"{_COLUMN} BETWEEN 0 AND 365"


def refuse_unfilled_profiles(bind) -> None:
    """Raise when any salary profile is left without a gap after the backfill.

    The backfill sets every row, so none can be left; this is the three-step
    rule's verification, stated rather than assumed.

    Args:
        bind: The migration's connection.

    Raises:
        RuntimeError: Naming every unfilled profile, with the SELECT that
            finds them.
    """
    rows = bind.execute(sa.text(
        f"SELECT id, user_id FROM {_SCHEMA}.{_TABLE} "
        f"WHERE {_COLUMN} IS NULL ORDER BY id"
    )).all()
    if rows:
        named = ", ".join(f"id={row.id} user_id={row.user_id}" for row in rows)
        raise RuntimeError(
            f"{len(rows)} salary profile(s) have no {_COLUMN} after the "
            f"backfill ({named}); cannot make it NOT NULL.  Inspect: SELECT "
            f"id, user_id FROM {_SCHEMA}.{_TABLE} WHERE {_COLUMN} IS NULL;"
        )


def upgrade():
    """Add the gap, fill every existing job with the developer's 6, then bound it."""
    bind = op.get_bind()
    op.add_column(
        _TABLE, sa.Column(_COLUMN, sa.Integer(), nullable=True), schema=_SCHEMA,
    )
    bind.execute(
        sa.text(f"UPDATE {_SCHEMA}.{_TABLE} SET {_COLUMN} = :days"),
        {"days": _BACKFILL_DAYS},
    )
    refuse_unfilled_profiles(bind)
    op.alter_column(
        _TABLE, _COLUMN, existing_type=sa.Integer(), nullable=False,
        schema=_SCHEMA,
    )
    op.create_check_constraint(_CHECK, _TABLE, _CHECK_PREDICATE, schema=_SCHEMA)


def downgrade():
    """Drop the bound and the gap; nothing at the older revision reads it."""
    op.drop_constraint(_CHECK, _TABLE, type_="check", schema=_SCHEMA)
    op.drop_column(_TABLE, _COLUMN, schema=_SCHEMA)
