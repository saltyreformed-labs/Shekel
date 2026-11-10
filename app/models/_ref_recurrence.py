"""The recurrence vocabulary's reference tables: a rule's unit, placement and shift.

Moved verbatim out of :mod:`app.models.ref` when the merge of ``dev`` into plan
step credit_card:CC-6 took that module past pylint's 1,000-line ceiling.  That
module re-exports all three, and it stays their one public path: the reseed
(``app.ref_seeds``) and the cache (``app.ref_cache``) find a reference table
by its class name on it, and ``shekel-private-module-import`` refuses an
import of this module from outside ``app.models``.
"""

from app.extensions import db


class RecurrenceUnit(db.Model):
    """Recurrence cadence-unit reference: period, week, month, year, due_date.

    The first axis of the two-axis recurrence model (redesign step R2): a
    rule recurs every ``budget.recurrence_rules.interval_n`` units of this
    kind.  Four of the closed pattern set's names were one idea with a
    different integer baked into the name (every 1 / 3 / 6 / 12 months);
    moving that integer into a column is what makes "every other month" and
    "every two years" expressible.  That set lived in ``ref.recurrence_patterns``
    until plan step **R9** dropped the table.

    Application code resolves these via ``ref_cache.recurrence_unit_id`` and
    compares against the integer ID -- never the string ``name`` -- matching
    the project-wide ``ref-table: IDs for logic, strings for display only``
    invariant.  See :class:`~app.enums.RecurrenceUnitEnum` for the per-value
    semantics.

    **Which units are measured in months lives HERE, and only here** (plan
    step plan#2302, ruling plan#2748): ``months_per_unit`` is the stride the
    occurrence engine walks (``recurrence._months.months_per_step``, read
    through ``ref_cache``), and ``has_day_of_month`` is GENERATED from it, so
    the flag a rule's CHECKs read and the stride the engine walks are one fact
    and cannot disagree.  It was a code table until that step, which a CHECK
    on ``budget.recurrence_rules`` could not see.

    **A span is a fact about the calendar, not a setting.**  Nothing in the
    application writes one and this table is not audited, so a hand edit leaves
    no record and moves every rule on the unit from the next restart; only one
    that flips the flag under a rule is refused.
    """

    __tablename__ = "recurrence_units"
    __table_args__ = (
        db.CheckConstraint(
            "months_per_unit IS NULL OR months_per_unit > 0",
            name="ck_recurrence_units_positive_months",
        ),
        # The superkey ``fk_recurrence_rules_unit_day_of_month`` targets, so
        # a rule's carried copy of the flag is held to its unit's.  It
        # constrains nothing (``id`` is the key); PostgreSQL requires a
        # UNIQUE over exactly the referenced columns.
        db.UniqueConstraint(
            "id", "has_day_of_month",
            name="uq_recurrence_units_id_has_day_of_month",
        ),
        {"schema": "ref"},
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(10), unique=True, nullable=False)
    # NULL for the paycheck, week and due-date units, which are not measured
    # in months at all: the absence IS the answer, and the flag below reads it.
    months_per_unit = db.Column(db.SmallInteger, nullable=True)
    has_day_of_month = db.Column(
        db.Boolean,
        db.Computed("months_per_unit IS NOT NULL", persisted=True),
        nullable=False,
    )

    def __repr__(self):
        return f"<RecurrenceUnit {self.name}>"


class PeriodPlacement(db.Model):
    """Occurrence-date -> pay-period placement reference (redesign step R2).

    An occurrence is a calendar date; a Shekel row lives in a pay period.
    This table names the rule that carries one to the other -- the axis
    today's ``Monthly`` and ``Monthly First`` patterns differ on (they differ
    on the anchor day as well; see :class:`~app.enums.PeriodPlacementEnum`),
    and therefore a real user choice rather than a derived detail.

    Application code resolves these via ``ref_cache.period_placement_id``
    and compares against the integer ID -- never the string ``name``.  See
    :class:`~app.enums.PeriodPlacementEnum` for the per-value semantics.
    """

    __tablename__ = "period_placements"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(30), unique=True, nullable=False)

    def __repr__(self):
        return f"<PeriodPlacement {self.name}>"


class BusinessDayShift(db.Model):
    """Weekend/holiday occurrence-shift reference (redesign step R2).

    Whether an occurrence falling on a non-business day moves backward, moves
    forward, or stays put.  Step R2 seeds the table and defaults every rule
    to ``none``; step R8 is the first step that lets a user pick another
    value, so that step turns behaviour on rather than adding a column.

    Application code resolves these via ``ref_cache.business_day_shift_id``
    and compares against the integer ID -- never the string ``name``.  See
    :class:`~app.enums.BusinessDayShiftEnum` for the per-value semantics and
    for why the shift applies to the cash date only.
    """

    __tablename__ = "business_day_shifts"
    __table_args__ = {"schema": "ref"}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(10), unique=True, nullable=False)

    def __repr__(self):
        return f"<BusinessDayShift {self.name}>"
