"""A rule states how its first date is read: budget.recurrence_rules gains weekday_reading

Plan step **recurrence:R8-c** (plan#1252), rulings **plan#2668** (amending
**R-R25**, plan#211) and **plan#2670**.

Review: Josh, 2026-08-16 (R-R25, plan#211: ``budget.recurrence_weekday_anchors``
is DROPPED unwritten and the coordinate goes on the rule) and 2026-11-02
(plan#2668: the table is still dropped, and the rule stores only how its first
date is read).

**What changes**::

    budget.recurrence_rules
        + weekday_reading VARCHAR(10) NULL      'from_start' | 'from_end'
        + ck_recurrence_rules_weekday_reading   the value, and the position
                                                each value needs of starts_on
        + ck_recurrence_rules_weekday_reading_unit
                                                only on a unit with a day of
                                                the month
        + ck_recurrence_rules_one_month_reading never beside nominal_day
    budget.recurrence_weekday_anchors           DROPPED, with its audit trigger

**What the column holds.**  A MONTH or YEAR rule's first occurrence
``starts_on`` can be read three ways, and the date cannot say which: by its day
of the month (absence, every rule before this revision), by its weekday counted
from the month's start (``'from_start'``: Tue 2026-11-17 is "the third
Tuesday"), or as the month's last such weekday (``'from_end'``).  The week
number and the weekday come from ``starts_on``, so neither is stored and
neither can disagree with it (plan#2668; ``CLAUDE.md`` rule 14).  This is why
the ruling's two columns, week number and weekday, became one.

**The same-row CHECKs, and why the value is not a ``ref`` id.**  Each value
needs a position of ``starts_on``:

* ``'from_start'`` -- the 28th or earlier.  Four whole weeks fit in the
  shortest month, so every month holds a 1st-4th of every weekday and only some
  hold a 5th; there is no fifth (plan#2670), so a 29th-31st date reads as its
  day or as the last weekday.
* ``'from_end'`` -- within a week of its month's end, so the date IS its
  month's last such weekday.

A CHECK can state that only if it can name which value it is checking, and
``budget.recurrence_rules`` holds no CHECK comparing a ``ref`` id to a literal
(a literal id would tie the table to a seed: the model's own rule beside
``max_per_month``).  So the column holds the two literals, and the one CHECK
naming them refuses any third value as well.  The exclusive arc with
``nominal_day`` is its own CHECK: a nominal day reads the date by its clamped
day of the month, and one date is read one way (R-R25).

**The unit half reads the flag below this revision** (ruling plan#2704): only a
MONTH or YEAR rule has a month coordinate, and which unit a row names is a
``ref`` id, so ``ck_recurrence_rules_weekday_reading_unit`` reads
``unit_has_day_of_month``, the flag ``9b3e6d1f4a27`` carries onto the row under
a composite key for ``nominal_day``'s and ``max_per_month``'s unit CHECKs.  It
lands here rather than in a revision of its own because it is one more rule of
this one column, so the column never exists without it.  The column opens all
NULL, so no stored row can fail it and no census runs first.
``app.services.recurrence._month_coordinate._require_weekday_reading_pair``
still answers first; this holds a writer that never reaches it.

**The table goes unwritten.**  ``budget.recurrence_weekday_anchors`` was
created empty at plan step R2b to hold ``(nth_week, weekday)`` and never gained
a writer; R-R25 put the coordinate on the rule, where the exclusive arc with
``nominal_day`` is a CHECK at all (PostgreSQL's CHECK reads one row).  Measured
read-only on production 2026-11-02 20:52 UTC: 0 rows in it, 57 rules, none
carrying a ``nominal_day``.  :func:`refuse_written_weekday_anchors` refuses to
drop a row anyway, naming each, so a database that did write one is not
silently re-read by its day of the month.  It leaves
``app.audit_infrastructure.AUDITED_TABLES`` in the same commit, so
``EXPECTED_TRIGGER_COUNT`` moves down by its one trigger.

**The downgrade refuses what it cannot keep, and restores the trigger.**
Dropping the column would re-read each rule carrying a reading by its date's
day of the month -- a third-Tuesday rule started on the 17th would fire on the
17th from then on -- so :func:`refuse_weekday_readings` refuses while any rule
carries one.  The table comes back empty with its constraints AND its audit
trigger, the shape ``d7b2f9a4c1e6`` takes: the image of the revision below
lists the table in ``AUDITED_TABLES``, and its deploy check
(``require_audit_triggers``) refuses a database missing any listed trigger.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '897a6663d221'
# The head of ``dev`` at ``f52607f8c``, measured on this branch after its merge
# (229 revisions, one head, by alembic.script.ScriptDirectory).  It was
# ``0703995c8536`` at authoring, until PR #651 landed ``9b3e6d1f4a27``, and it
# must stay above that revision: ``ck_recurrence_rules_weekday_reading_unit``
# reads the flag it carries.  Another lane landing a revision first makes two
# heads, which git merges cleanly and ``flask db upgrade`` refuses: re-point
# this at the measured head as the last edit before the pull request.
down_revision = '9b3e6d1f4a27'
branch_labels = None
depends_on = None


#: The column's two values, and the CHECK naming them.  Spelled here rather
#: than imported because a migration imports nothing from ``app``: the SQL a
#: revision ran is frozen with it.  ``EXTRACT(day FROM <date>)`` is IMMUTABLE
#: for a ``date`` argument and ``date_trunc`` is cast through ``::timestamp``
#: for the same reason, as ``ck_recurrence_rules_nominal_day`` does, which is
#: what lets both appear in a CHECK.  28 is four whole weeks, the shortest
#: month; 7 is one week.
_WEEKDAY_READING_CHECK = (
    "weekday_reading IS NULL "
    "OR (weekday_reading = 'from_start' "
    "AND EXTRACT(day FROM starts_on) <= 28) "
    "OR (weekday_reading = 'from_end' "
    "AND EXTRACT(day FROM starts_on) + 7 > "
    "EXTRACT(day FROM (date_trunc('month', starts_on::timestamp) "
    "+ INTERVAL '1 month - 1 day')))"
)

#: Only a unit with a day of the month reads a weekday of a month.
_WEEKDAY_READING_UNIT_CHECK = "weekday_reading IS NULL OR unit_has_day_of_month"

#: The exclusive arc: one date is read one way.
_ONE_MONTH_READING_CHECK = "weekday_reading IS NULL OR nominal_day IS NULL"

#: Every row the drop would lose, with enough of it to find.
_WRITTEN_ANCHORS_SQL = """
SELECT a.id, a.recurrence_rule_id, a.nth_week, a.weekday
FROM budget.recurrence_weekday_anchors a
ORDER BY a.id
"""

#: Every rule the downgrade would re-read by its day of the month.
_RULES_READ_BY_WEEKDAY_SQL = """
SELECT r.id,
       r.weekday_reading,
       r.starts_on,
       r.transaction_template_id,
       r.transfer_template_id,
       r.paycheck_line_id
FROM budget.recurrence_rules r
WHERE r.weekday_reading IS NOT NULL
ORDER BY r.id
"""


def refuse_written_weekday_anchors(bind) -> None:
    """Raise when ``budget.recurrence_weekday_anchors`` holds any row.

    Args:
        bind: A SQLAlchemy connection or session bind.

    Raises:
        RuntimeError: Naming every row, its rule and the coordinate it holds.
    """
    written = bind.execute(sa.text(_WRITTEN_ANCHORS_SQL)).all()
    if not written:
        return
    raise RuntimeError(
        "budget.recurrence_weekday_anchors holds row(s): "
        + "; ".join(
            f"id={row.id} recurrence_rule_id={row.recurrence_rule_id} "
            f"nth_week={row.nth_week} weekday={row.weekday}"
            for row in written
        )
        + ".  This revision drops the table (rulings R-R25 and plan#2668: a "
        "rule states how its first date is read on its own row), and no "
        "application code ever wrote it, so these rows are a hand edit or a "
        "restore.  Dropping them would re-read each rule by its first date's "
        "day of the month.  Re-state each rule's coordinate, delete the rows, "
        "and re-run."
    )


def refuse_weekday_readings(bind) -> None:
    """Raise when any rule is read by its weekday.

    Args:
        bind: A SQLAlchemy connection or session bind.

    Raises:
        RuntimeError: Naming every such rule, its reading, its first
            occurrence and the definition that owns it.
    """
    offenders = bind.execute(sa.text(_RULES_READ_BY_WEEKDAY_SQL)).all()
    if not offenders:
        return
    raise RuntimeError(
        "recurrence rule(s) are read by their weekday: "
        + "; ".join(
            f"id={row.id} weekday_reading={row.weekday_reading} "
            f"starts_on={row.starts_on} "
            f"transaction_template_id={row.transaction_template_id} "
            f"transfer_template_id={row.transfer_template_id} "
            f"paycheck_line_id={row.paycheck_line_id}"
            for row in offenders
        )
        + ".  Revision 897a6663d221 is what made a weekday reading storable, "
        "and below it each of these would fire on its first date's day of the "
        "month: a third-Tuesday rule started on the 17th would fire on the "
        "17th.  Re-state each one by its day of the month or delete it, or "
        "REVERT THE DATA BY RESTORING THE DATABASE."
    )


def upgrade():
    """Refuse a written anchor, drop the table, then add the column and CHECKs."""
    refuse_written_weekday_anchors(op.get_bind())
    # Explicit, though dropping the table would take it: the intent is visible
    # and the step is idempotent if the trigger is already absent.
    op.execute(
        "DROP TRIGGER IF EXISTS audit_recurrence_weekday_anchors "
        "ON budget.recurrence_weekday_anchors"
    )
    op.drop_table("recurrence_weekday_anchors", schema="budget")

    op.add_column(
        "recurrence_rules",
        sa.Column("weekday_reading", sa.String(length=10), nullable=True),
        schema="budget",
    )
    op.create_check_constraint(
        "ck_recurrence_rules_weekday_reading", "recurrence_rules",
        _WEEKDAY_READING_CHECK, schema="budget",
    )
    op.create_check_constraint(
        "ck_recurrence_rules_weekday_reading_unit", "recurrence_rules",
        _WEEKDAY_READING_UNIT_CHECK, schema="budget",
    )
    op.create_check_constraint(
        "ck_recurrence_rules_one_month_reading", "recurrence_rules",
        _ONE_MONTH_READING_CHECK, schema="budget",
    )


def downgrade():
    """Refuse a weekday reading, drop the column, and restore the empty table."""
    refuse_weekday_readings(op.get_bind())
    op.drop_constraint(
        "ck_recurrence_rules_one_month_reading", "recurrence_rules",
        type_="check", schema="budget",
    )
    op.drop_constraint(
        "ck_recurrence_rules_weekday_reading_unit", "recurrence_rules",
        type_="check", schema="budget",
    )
    op.drop_constraint(
        "ck_recurrence_rules_weekday_reading", "recurrence_rules",
        type_="check", schema="budget",
    )
    op.drop_column("recurrence_rules", "weekday_reading", schema="budget")

    # As revision c8f2b6a41d93 created it.
    op.create_table(
        "recurrence_weekday_anchors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("recurrence_rule_id", sa.Integer(), nullable=False),
        sa.Column("nth_week", sa.Integer(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "nth_week BETWEEN -1 AND 5 AND nth_week <> 0",
            name="ck_recurrence_weekday_anchors_nth_week",
        ),
        sa.CheckConstraint(
            "weekday BETWEEN 0 AND 6",
            name="ck_recurrence_weekday_anchors_weekday",
        ),
        sa.ForeignKeyConstraint(
            ["recurrence_rule_id"], ["budget.recurrence_rules.id"],
            name="fk_recurrence_weekday_anchors_rule_id", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recurrence_rule_id", name="uq_recurrence_weekday_anchors_rule",
        ),
        schema="budget",
    )
    op.execute(
        "DROP TRIGGER IF EXISTS audit_recurrence_weekday_anchors "
        "ON budget.recurrence_weekday_anchors"
    )
    op.execute(
        "CREATE TRIGGER audit_recurrence_weekday_anchors "
        "AFTER INSERT OR UPDATE OR DELETE ON budget.recurrence_weekday_anchors "
        "FOR EACH ROW EXECUTE FUNCTION system.audit_trigger_func()"
    )
