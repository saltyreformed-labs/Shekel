"""a raise states the date it starts

Plan step **salary plan#3048** (task "A raise states the date it starts"),
rulings **plan#3090** ("A raise states the date it starts; a recurring raise
repeats on that date") and **plan#3094** ("The raise's month and year columns
are deleted once copied into its start date").

Review: Josh, 2026-11-09 (ruling plan#3094: "Yes, delete them once they're
copied").

**Why the day matters now.**  The developer's employer pays each paycheck for
a two-week period, each weekday at the pay in force that day (ruling
**plan#3089**), so the day a raise starts moves money: a raise from Monday
07-06 instead of Wednesday 07-01 leaves three more of a straddling paycheck's
ten weekdays at the old pay.  A raise stated only a MONTH, which the engine
read as its 1st, so a mid-month raise could not be stored at all.

**What it does.**  ``salary.salary_raises.effective_date`` (DATE) replaces
``effective_year`` and ``effective_month``:

1. the column is added nullable and every row is backfilled with
   ``make_date(effective_year, effective_month, 1)`` -- the day the engine
   has always landed a raise on (``salary_raises.applications_between``: "An
   application lands on the 1st of its effective month"), so no figure moves;
2. a row still NULL after the backfill is refused by id, then the column is
   made NOT NULL;
3. the four constraints over the pair are dropped with the pair -- the
   duplicate guard ``uq_salary_raises_profile_type_year_month``, the month
   CHECK, the year-window CHECK and the end-year ordering CHECK -- and the
   date takes their places: the 2000-2100 window as a date window, the end
   year not before the date's year, the duplicate guard over
   ``(salary_profile_id, raise_type_id, effective_date)``;
4. one rule is new, because the date can state what the pair could not: a
   RECURRING raise may not start on February 29, since it repeats on its own
   month and day each year and three years in four have no such day.

**The duplicate guard narrows from a month to a day**, which is the design
rather than a loss.  Its purpose is F-051's double submit -- the same form
posted twice -- and a double submit posts the same date.  Two raises of one
type on two days of one month are two stated facts under a date key, where the
month key refused the second as a duplicate of the first.

**The downgrade rebuilds the month and year from the date and REFUSES, naming
the rows, while any raise starts on a day other than the 1st**: the old
columns cannot hold the day, and dropping it silently would move that raise's
first paychecks.  A raise on the 1st round-trips exactly, and since the date
key is unique, raises all on the 1st cannot collide under the month key.

Revision ID: 6740ce13e174
Revises: 897a6663d221
Create Date: 2026-11-09
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '6740ce13e174'
down_revision = '897a6663d221'
branch_labels = None
depends_on = None

_TABLE = "salary_raises"
_SCHEMA = "salary"

#: The duplicate guard over the pair, and the one over the date.
_UQ_PAIR = "uq_salary_raises_profile_type_year_month"
_UQ_PAIR_COLUMNS = [
    "salary_profile_id", "raise_type_id", "effective_year", "effective_month",
]
_UQ_DATE = "uq_salary_raises_profile_type_date"
_UQ_DATE_COLUMNS = ["salary_profile_id", "raise_type_id", "effective_date"]

#: The CHECKs over the pair, as ``(name, predicate)`` in creation order --
#: exactly what the head before this revision carries (``c9f1a7b3d2e8`` for
#: the year window, ``e5f6a7b8c9d0`` for the month, ``c9a4e17b53d8`` for the
#: ordering), so the downgrade restores them by these same rows.
_PAIR_CHECKS = (
    (
        "ck_salary_raises_valid_month",
        "effective_month >= 1 AND effective_month <= 12",
    ),
    (
        "ck_salary_raises_valid_effective_year",
        "effective_year >= 2000 AND effective_year <= 2100",
    ),
    (
        "ck_salary_raises_terminal_year_not_before_effective",
        "terminal_year IS NULL OR terminal_year >= effective_year",
    ),
)

#: The CHECKs over the date, in creation order.  The ordering CHECK keeps its
#: name: it states the same rule, a raise cannot end before it starts.
_DATE_CHECKS = (
    (
        "ck_salary_raises_valid_effective_date",
        "effective_date >= DATE '2000-01-01' AND effective_date <= DATE '2100-12-31'",
    ),
    (
        "ck_salary_raises_terminal_year_not_before_effective",
        "terminal_year IS NULL OR "
        "terminal_year >= EXTRACT(YEAR FROM effective_date)",
    ),
    (
        "ck_salary_raises_recurring_not_on_february_29",
        "NOT (is_recurring AND EXTRACT(MONTH FROM effective_date) = 2 "
        "AND EXTRACT(DAY FROM effective_date) = 29)",
    ),
)


def refuse_undated_raises(bind) -> None:
    """Raise when any raise is left without a start date after the backfill.

    The backfill reads two NOT NULL columns, so no row can be left; this is
    the three-step rule's verification, stated rather than assumed.

    Args:
        bind: The migration's connection.

    Raises:
        RuntimeError: Naming every undated raise, with the SELECT that finds
            them.
    """
    rows = bind.execute(sa.text(
        "SELECT id, salary_profile_id FROM salary.salary_raises "
        "WHERE effective_date IS NULL ORDER BY id"
    )).all()
    if rows:
        named = ", ".join(
            f"id={row.id} salary_profile_id={row.salary_profile_id}" for row in rows
        )
        raise RuntimeError(
            f"{len(rows)} salary raise(s) have no start date after the backfill "
            f"({named}); cannot make effective_date NOT NULL.  Inspect: SELECT "
            "id, salary_profile_id, effective_year, effective_month FROM "
            "salary.salary_raises WHERE effective_date IS NULL;"
        )


def refuse_raises_off_the_first(bind) -> None:
    """Raise when any raise starts on a day the month-and-year columns cannot hold.

    Args:
        bind: The migration's connection.

    Raises:
        RuntimeError: Naming every raise that starts on a day other than the
            1st, which the downgrade would otherwise move to the 1st.
    """
    rows = bind.execute(sa.text(
        "SELECT id, salary_profile_id, effective_date FROM salary.salary_raises "
        "WHERE EXTRACT(DAY FROM effective_date) <> 1 ORDER BY id"
    )).all()
    if rows:
        named = ", ".join(
            f"id={row.id} salary_profile_id={row.salary_profile_id} "
            f"effective_date={row.effective_date.isoformat()}"
            for row in rows
        )
        raise RuntimeError(
            f"Cannot downgrade: {len(rows)} salary raise(s) start on a day "
            f"other than the 1st ({named}), and the month-and-year columns "
            "this downgrade restores cannot hold a day (ruling plan#3090).  "
            "Re-date each to the 1st of its month, or delete it, first."
        )


def upgrade():
    """Copy each raise's month and year into its start date, then drop the pair."""
    bind = op.get_bind()
    op.add_column(
        _TABLE, sa.Column("effective_date", sa.Date(), nullable=True),
        schema=_SCHEMA,
    )
    bind.execute(sa.text(
        "UPDATE salary.salary_raises "
        "SET effective_date = make_date(effective_year, effective_month, 1)"
    ))
    refuse_undated_raises(bind)
    op.alter_column(
        _TABLE, "effective_date", existing_type=sa.Date(), nullable=False,
        schema=_SCHEMA,
    )

    op.drop_constraint(_UQ_PAIR, _TABLE, type_="unique", schema=_SCHEMA)
    for name, _ in reversed(_PAIR_CHECKS):
        op.drop_constraint(name, _TABLE, type_="check", schema=_SCHEMA)
    op.drop_column(_TABLE, "effective_month", schema=_SCHEMA)
    op.drop_column(_TABLE, "effective_year", schema=_SCHEMA)

    for name, predicate in _DATE_CHECKS:
        op.create_check_constraint(name, _TABLE, predicate, schema=_SCHEMA)
    op.create_unique_constraint(_UQ_DATE, _TABLE, _UQ_DATE_COLUMNS, schema=_SCHEMA)


def downgrade():
    """Rebuild each raise's month and year from its start date, then drop the date."""
    bind = op.get_bind()
    refuse_raises_off_the_first(bind)

    op.add_column(
        _TABLE, sa.Column("effective_month", sa.Integer(), nullable=True),
        schema=_SCHEMA,
    )
    op.add_column(
        _TABLE, sa.Column("effective_year", sa.Integer(), nullable=True),
        schema=_SCHEMA,
    )
    bind.execute(sa.text(
        "UPDATE salary.salary_raises SET "
        "effective_year = EXTRACT(YEAR FROM effective_date)::INT, "
        "effective_month = EXTRACT(MONTH FROM effective_date)::INT"
    ))
    for column in ("effective_month", "effective_year"):
        op.alter_column(
            _TABLE, column, existing_type=sa.Integer(), nullable=False,
            schema=_SCHEMA,
        )

    op.drop_constraint(_UQ_DATE, _TABLE, type_="unique", schema=_SCHEMA)
    for name, _ in reversed(_DATE_CHECKS):
        op.drop_constraint(name, _TABLE, type_="check", schema=_SCHEMA)
    op.drop_column(_TABLE, "effective_date", schema=_SCHEMA)

    for name, predicate in _PAIR_CHECKS:
        op.create_check_constraint(name, _TABLE, predicate, schema=_SCHEMA)
    op.create_unique_constraint(_UQ_PAIR, _TABLE, _UQ_PAIR_COLUMNS, schema=_SCHEMA)
