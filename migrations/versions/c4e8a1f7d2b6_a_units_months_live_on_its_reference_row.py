"""A unit's months live on its reference row; its day-of-month flag is generated from them

Plan step plan#2302, ruling plan#2748 (developer, 2026-11-03: "Build option
A").  Which recurrence units are measured in months, and how many, was a code
table (``recurrence._months._MONTHS_PER_UNIT``: month 1, year 12, paycheck and
week absent) that every refusal of a unit pairing derived from.  A CHECK on
``budget.recurrence_rules`` cannot read a code table, so the pairings it
governs (a per-month ceiling, a nominal day) were held only by doors.  This
migration gives the fact its one home on ``ref.recurrence_units``:

* ``months_per_unit smallint NULL``: 1 for ``month``, 12 for ``year`` (the
  application's ``MONTHS_PER_YEAR``), NULL for ``period`` and ``week``, which
  are not measured in months at all.  Under
  ``ck_recurrence_units_positive_months``: the engine walks it as a ``range``
  step and divides twelve by it, and a zero raises from both.
* ``has_day_of_month boolean NOT NULL GENERATED ALWAYS AS (months_per_unit IS
  NOT NULL) STORED``: the flag a rule's CHECKs will read, generated so it
  cannot disagree with the stride the engine walks.
* ``uq_recurrence_units_id_has_day_of_month``: the superkey a rule's composite
  key onto that pair needs (the next migration).  It constrains nothing,
  ``id`` being the key.

The spans are written by NAME, as ``e7a4d95c2b18`` seeded the rows, and the
upgrade refuses unless exactly the two calendar rows took one: a database
missing either would leave the engine with no stride for a live cadence.
Measured on the production clone ``shekel_r8c`` (dump of 2026-11-03): the
four rows ``period``/``week``/``month``/``year`` at ids 1-4.

The downgrade drops all three; the spans are recomputed by the upgrade.  It
moves no money in either direction.

Self-contained dependency policy: this migration imports nothing from ``app``.

Revision ID: c4e8a1f7d2b6
Revises: 4d4eb9cd3850
Create Date: 2026-11-03
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c4e8a1f7d2b6"
down_revision = "4d4eb9cd3850"
branch_labels = None
depends_on = None

_TABLE = "recurrence_units"
_CHECK = "ck_recurrence_units_positive_months"
_SUPERKEY = "uq_recurrence_units_id_has_day_of_month"

#: The two calendar units' spans.  ``12`` is the application's
#: ``MONTHS_PER_YEAR``, spelled here because a migration is a frozen record
#: and imports nothing from the code it outlives.
_SPANS = {"month": 1, "year": 12}

_WRITE_SPANS = sa.text(
    "UPDATE ref.recurrence_units SET months_per_unit = CASE name "
    "WHEN 'month' THEN :month WHEN 'year' THEN :year END "
    "WHERE name IN ('month', 'year')"
)


def upgrade():
    """Add the span, write the two calendar units', then generate the flag."""
    op.add_column(
        _TABLE,
        sa.Column("months_per_unit", sa.SmallInteger(), nullable=True),
        schema="ref",
    )
    op.create_check_constraint(
        _CHECK, _TABLE, "months_per_unit IS NULL OR months_per_unit > 0",
        schema="ref",
    )
    written = op.get_bind().execute(_WRITE_SPANS, _SPANS).rowcount
    if written != len(_SPANS):
        raise RuntimeError(
            f"ref.recurrence_units: wrote a month span on {written} row(s), "
            f"expected {len(_SPANS)} (the 'month' and 'year' rows "
            f"e7a4d95c2b18 seeds).  A database missing either leaves a live "
            f"calendar cadence with no stride.  Diagnose with: SELECT id, name "
            f"FROM ref.recurrence_units ORDER BY id;"
        )
    op.add_column(
        _TABLE,
        sa.Column(
            "has_day_of_month",
            sa.Boolean(),
            sa.Computed("months_per_unit IS NOT NULL", persisted=True),
            nullable=False,
        ),
        schema="ref",
    )
    op.create_unique_constraint(
        _SUPERKEY, _TABLE, ["id", "has_day_of_month"], schema="ref",
    )


def downgrade():
    """Drop the superkey, the flag, the CHECK and the span."""
    op.drop_constraint(_SUPERKEY, _TABLE, schema="ref", type_="unique")
    op.drop_column(_TABLE, "has_day_of_month", schema="ref")
    op.drop_constraint(_CHECK, _TABLE, schema="ref", type_="check")
    op.drop_column(_TABLE, "months_per_unit", schema="ref")
