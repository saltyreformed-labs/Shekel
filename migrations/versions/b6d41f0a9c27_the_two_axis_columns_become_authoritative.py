"""the two-axis columns become authoritative

Plan step **R7c-b** of ``docs/plans/implementation_plan_recurrence_redesign.md``
section 4 -- the MIGRATE half of an expand / migrate / contract.  Plan step
R7c-a added five columns, backfilled them and had the write door keep them in
step while NOTHING read them; this leaf moves every reader across and locks the
columns down behind them.  Plan step R7c-c drops the closed set they replace.

Review: Josh, 2026-08-14 -- APPROVED at R7c-a: the D28 ruling that ``starts_on``
is the rule's FIRST OCCURRENCE (one meaning for every unit), the three-leaf
split with the destructive DDL last, and this leaf carrying the ``NOT NULL``
tighten and the ``end_date >= starts_on`` CHECK.
Review: Josh, 2026-08-15 -- APPROVED: the form authors one date plus a
conditional day, and ``ck_recurrence_rules_nominal_day`` is COMPLETED with the
clamp equality so the runtime guard that stood in for it becomes structurally
unnecessary.
Review: Josh, 2026-08-15 -- APPROVED, REVERSING the ``end_date >= starts_on``
half of the first line: ``ck_recurrence_rules_valid_window`` is HELD BACK from
this leaf.  See "The window CHECK is not here, and why" below.

What it does, and why each half waited for this leaf
----------------------------------------------------

1. **RE-RUNS R7c-a's backfill**, the identical statement out of
   :mod:`migrations._recurrence_two_axis_backfill`.  That leaf's dual write
   refreshes the two-axis columns on every RULE write and on no other event, so
   a pay schedule REBUILT between the two leaves moves the derivation and leaves
   the column where it was -- ``loan_recurrence_sync._sync_loan_cadence`` makes
   the window plain, since it returns early when the loan's own facts have not
   moved.  Nothing read the column in between, so nothing was wrong; from this
   migration the stored value is authoritative, and a stale one would FREEZE a
   first occurrence the app no longer derives.  Re-running the identical text
   makes the cutover a no-op by construction rather than by argument.

2. **TIGHTENS four columns to NOT NULL** -- ``unit_id``, ``placement_id``,
   ``shift_id`` and ``starts_on``.  This is the third step of the documented
   three (``.claude/rules/database.md``: add nullable, backfill, tighten) and it
   belongs with the leaf that makes the columns MATTER: nothing read them at
   R7c-a, so a NULL was invisible; every reader takes them now, so a NULL is a
   wrong answer.  ``nominal_day`` stays nullable and always will -- its ABSENCE
   is the discriminator (ruling R-R3), meaning "the date holds the day itself".

3. **COMPLETES ``ck_recurrence_rules_nominal_day``** with the clamp equality.
   The R7c-a form bounded the domain (29-31) and required the nominal day to
   exceed the date's, which admits ``(starts_on = 2026-04-15, nominal_day = 30)``
   -- a nominal day beside a date that was never clamped.  Only a runtime guard
   in ``recurrence._occurrence._require_generable`` caught that, which is a
   fence over a state the schema should make unrepresentable.  Adding
   ``EXTRACT(day FROM starts_on) = LEAST(nominal_day, <that month's last day>)``
   closes it, and the guard is DELETED in the same commit.  Verified IMMUTABLE
   on the live server rather than assumed: ``date_trunc(text, timestamp)`` is
   immutable where the ``timestamptz`` overload is only stable, which is why
   ``starts_on`` is cast explicitly -- the same overload trap R7c-a's own review
   caught.

4. **ADDS ``ck_recurrence_rules_starts_on_range``**: the first occurrence falls
   inside the calendar this application reaches, 2000-01-01..2100-12-31 -- the
   same two dates ``ck_template_amount_versions_effective_date_range`` bounds
   the other user-authored date with, now named once in
   ``app.utils.dates.CALENDAR_DATE_MIN`` / ``_MAX``.  It backs a MEASURED 500:
   past the saved horizon the pay calendar projects the covering paycheck by
   adding ``cadence_days`` to a start, so ``?starts_on=9999-12-31`` on the
   recurrence-preview endpoint -- which reads the value from ``request.args``,
   where no schema stands -- raised ``OverflowError: date value out of range``
   from ``pay_calendar._calendar._projected_after`` for any signed-in user.
   ``_resolution._require_authored_start_window`` is the door-side mirror; this
   is the backstop behind it.

The window CHECK is not here, and why (developer ruling, 2026-08-15)
--------------------------------------------------------------------

``ck_recurrence_rules_valid_window`` -- ``end_date >= starts_on`` -- was drafted
into this leaf and is HELD BACK.  It asserts an invariant the table does not
have yet, because these columns hold two different kinds of fact:

* what a USER AUTHORS about a repeating definition, where a stop before the
  start is a mistake to report;
* what the APP DERIVES for a recurring loan payment, where an EMPTY window is a
  legitimate and sometimes correct answer.  ``loan_recurrence_sync`` writes
  ``starts_on`` = the loan's first contractual installment and ``end_date`` =
  its derived payoff, and a loan paid off BEFORE its first installment inverts
  the pair honestly.  Measured: originate 2026-08-01 with ``payment_day`` 1, so
  the first installment is 2026-09-01; true the balance to zero on 2026-08-15
  and ``recurrence_end_date`` answers ``as_of`` = 2026-08-15.  Forward
  generation emits nothing, which is exactly right -- the loan owes nothing --
  and the CHECK turns that correct state into an unhandled ``CheckViolation``
  out of the true-up, a params edit, or any transfer settle.

Both local repairs are worse than the state.  Clamping the bound up to
``max(as_of, starts_on)`` admits ONE occurrence, so a paid-off loan keeps a
projected payment whose cash still debits while the fold books the whole amount
to Refund.  Archiving the template from inside a sync is a destructive side
effect on a path that runs on every settle, and a corrected true-up would not
undo it.

So the invariant is held at the two AUTHORING doors instead, which is where a
user's mistake can be reported at all:
``schemas/validation/_helpers.require_end_bound_after_start`` for a create, and
``_recurrence_form_helpers.refuse_inverted_window`` for an update, which
compares the EFFECTIVE start against the EFFECTIVE bound and so also covers the
submission that moves ``starts_on`` PAST a stored ``end_date`` -- a case no
schema can see.  The CHECK lands with the step that stops persisting the
derived window (``loan_recurrence_sync`` becomes a resolver and generation
applies the loan's window over the rule's own), at which point every row is
user-authored and the constraint is unconditionally true.

Measured on a 2026-08-15 clone of production (46 live rules)
------------------------------------------------------------

**Re-measured on a FRESH clone, and this revision RUN end to end against it**
(2026-08-15).  An earlier draft of this block was taken on a database migrated
from an earlier draft of this file -- it carried only two of the constraints,
so the third and ``refuse_out_of_range_starts`` were unbacked, and one claim
below was simply wrong.  The clone this states was dumped from
``shekel-prod-db`` at ``alembic_version = c4e1a8b70f36`` (before either R7c
leaf) and upgraded to head.

* 0 rows hold a NULL in any of the four columns being tightened, so the tighten
  runs clean;
* 0 rows carry a ``nominal_day`` at all, so the completed CHECK admits every
  live row trivially.  It was therefore probed against real data rather than
  argued: rule id=2 starts 2026-03-26, a day MARCH holds, and setting
  ``nominal_day = 30`` on it is refused by
  ``ck_recurrence_rules_nominal_day`` -- which is the ``(2026-04-15, 30)``
  shape the R7c-a predicate admitted;
* the 5 end-dated rules span 96, 8280, 1037, 1037 and 1006 days.  **The first
  is three MONTHS, not years**, which an earlier draft of this block claimed of
  all five; it is recorded because a "years apart" reading is what would make
  the window comparison look untestable on live data;
* 0 rows carry a ``starts_on`` outside 2000-2100, so the calendar-window CHECK
  admits every live row -- the 46 span 2026-03-01 to 2027-03-16, comfortably
  inside it;
* the re-backfill in item 1 moved NOTHING: the same 46 rows, the same span, the
  same 0 nulls before and after, which is the no-op the cutover needs it to be.

Each is CHECKED rather than trusted: a state a constraint would refuse is named
with its rule ids before the DDL runs, because ``ALTER TABLE ... ADD CHECK``
reports the table and not the row.

**Downgrade RAISES**, and the reason is the re-backfill above rather than the
DDL.  Loosening the constraints is loss-free in itself; what is not safe is
what an operator does next.  Re-``upgrade`` would run item 1 again, over rules
this very revision's write door authored -- and that door does not write
``start_date`` or ``month_of_year``, two of the three columns the derivation
reads.  Measured on this suite's fixtures: a MONTHLY rent authored to first
occur ``2026-09-15`` comes back ``2024-01-15``, backdated 32 months onto the
schedule's opening, which is the backdated-generation shape
``_recurrence_form_render.create_form_default_starts_on`` exists to make
unreachable.

Restoring the legacy columns from ``starts_on`` inside the downgrade was
weighed and REJECTED: it round-trips every cadence except ``Monthly First``,
whose derivation scans the schedule's months rather than reading a day, so the
closed set cannot express "the 15th, funded from that month's first paycheck"
-- measured, ``2026-03-15`` comes back ``2026-04-01``.  A downgrade that is
faithful for six cadences and silently wrong for the seventh is worse than one
that refuses.

The refusal follows ``.claude/rules/database.md``'s stated alternative: it
names why it is unsafe and gives the literal SQL to loosen the schema by hand.
Reverting the DATA needs a restore.  Developer ruling, 2026-08-15.

Revision ID: b6d41f0a9c27
Revises: b7c3d9e1f204
Create Date: 2026-08-15

**Re-pointed off ``f2a94c7e1b60`` when this branch merged ``dev``**, which had
landed ``b7c3d9e1f204`` (a purchase is a ledger source) from the same parent.
Two heads, and the two touch different tables -- that one
``budget.purchases``, this one ``budget.recurrence_rules`` -- so the chain
LINEARISES rather than needing a merge revision. Safe because neither revision
has run in production: the clone this was measured on stamps
``c4e1a8b70f36``.
"""
from alembic import op
import sqlalchemy as sa

from migrations._recurrence_two_axis_backfill import (
    BACKFILL_SQL,
    refuse_underivable,
    verify_backfilled,
)


# revision identifiers, used by Alembic.
revision = "b6d41f0a9c27"
down_revision = "b7c3d9e1f204"
branch_labels = None
depends_on = None


#: The columns that stop being nullable, in the order the model declares them.
_TIGHTENED_COLUMNS: tuple[str, ...] = (
    "unit_id", "placement_id", "shift_id", "starts_on",
)

#: ``ck_recurrence_rules_nominal_day`` as plan step R7c-a wrote it: the domain
#: and the "exceeds the date's own day" half, with no statement that the date
#: was CLAMPED by that value.  Kept here because :func:`downgrade`'s refusal
#: QUOTES it -- an operator loosening the schema by hand needs the predicate
#: this revision replaced, and reading it off this file is what stops them
#: reconstructing it from the R7c-a migration and getting a conjunct wrong.
_NOMINAL_DAY_CHECK_R7C_A = (
    "nominal_day IS NULL OR ("
    "starts_on IS NOT NULL "
    "AND nominal_day BETWEEN 29 AND 31 "
    "AND nominal_day > EXTRACT(day FROM starts_on))"
)

#: The COMPLETE form.  Three conjuncts and none is implied by the others:
#:
#: * the domain -- every month holds its first 28 days, so only 29-31 can be
#:   lost;
#: * ``nominal_day > EXTRACT(day FROM starts_on)`` -- a value at or below the
#:   day the date already carries would be a SECOND statement of it, which is
#:   the two-representations defect ruling R-R16 removes;
#: * the CLAMP EQUALITY -- ``starts_on``'s own day must be exactly what clamping
#:   *nominal_day* into that month produces.  Taken with the two above this is
#:   what makes presence IMPLY that the clamp happened, so ``nominal_day IS NOT
#:   NULL`` has ONE meaning instead of "either the month was too short, or
#:   somebody wrote a number".
#:
#: The ``starts_on IS NOT NULL`` conjunct R7c-a needed is GONE: this migration
#: makes the column ``NOT NULL`` a few lines above, so it would be a clause that
#: can never be false -- the shape this project deletes rather than keeps
#: passing.
_NOMINAL_DAY_CHECK_COMPLETE = (
    "nominal_day IS NULL OR ("
    "nominal_day BETWEEN 29 AND 31 "
    "AND nominal_day > EXTRACT(day FROM starts_on) "
    "AND EXTRACT(day FROM starts_on) = LEAST(nominal_day, EXTRACT(day FROM ("
    "date_trunc('month', starts_on::timestamp) "
    "+ INTERVAL '1 month - 1 day'))))"
)

#: Rows the completed ``nominal_day`` CHECK would refuse.
#:
#: The clamp equality restated as a query, because ``ALTER TABLE ... ADD CHECK``
#: names the TABLE and the constraint and not the row: an operator meeting it
#: mid-deploy would have a failed migration and no way to see which rule to
#: repair.  R7c-a's own backfill cannot produce such a row -- it writes the day
#: only where it exceeds the date's -- so anything this finds was hand-edited or
#: restored.
_REFUSE_INCONSISTENT_NOMINAL_DAY_SQL = f"""
SELECT id, starts_on, nominal_day
FROM budget.recurrence_rules
WHERE NOT ({_NOMINAL_DAY_CHECK_COMPLETE})
ORDER BY id
"""

#: How far this application's calendar reaches, mirrored from
#: ``app.utils.dates.CALENDAR_DATE_MIN`` / ``_MAX``.  Literals rather than an
#: import: a migration states the schema as it was at this revision, and a
#: constant that later moves would silently re-date a shipped one.
_STARTS_ON_RANGE_CHECK = (
    "starts_on BETWEEN DATE '2000-01-01' AND DATE '2100-12-31'"
)

#: Rows the calendar-window CHECK would refuse.
#:
#: R7c-a's backfill cannot produce one -- it derives every date from a pay
#: period or from an existing bound, both inside the window -- so anything this
#: finds predates the two-axis columns or was hand-edited.
_REFUSE_OUT_OF_RANGE_START_SQL = f"""
SELECT id, starts_on
FROM budget.recurrence_rules
WHERE NOT ({_STARTS_ON_RANGE_CHECK})
ORDER BY id
"""


def refuse_inconsistent_nominal_days(bind) -> None:
    """Raise when any rule's nominal day disagrees with its first occurrence.

    Named rather than inline for the reason
    :func:`~migrations._recurrence_two_axis_backfill.refuse_underivable` is: a
    refusal nothing executes is a refusal nobody has seen work, and driving this
    one from a test does not need real DDL inside an xdist worker.

    Args:
        bind: A SQLAlchemy connection or session bind.

    Raises:
        RuntimeError: Naming every offending rule, its date and its day.
    """
    offenders = bind.execute(
        sa.text(_REFUSE_INCONSISTENT_NOMINAL_DAY_SQL),
    ).all()
    if not offenders:
        return
    raise RuntimeError(
        "recurrence rule(s) carry a nominal_day their own first occurrence "
        "did not clamp: "
        + "; ".join(
            f"id={row.id} starts_on={row.starts_on} "
            f"nominal_day={row.nominal_day}"
            for row in offenders
        )
        + ".  A nominal day records a day the first occurrence's month was too "
        "short to hold (ruling R-R3), so the date must be that month's last "
        "day and the value must exceed it.  Nothing this application writes "
        "can produce the pair; repair or NULL the column and re-run."
    )


def refuse_out_of_range_starts(bind) -> None:
    """Raise when any rule's first occurrence falls outside the app's calendar.

    Args:
        bind: A SQLAlchemy connection or session bind.

    Raises:
        RuntimeError: Naming every offending rule and its date.
    """
    offenders = bind.execute(sa.text(_REFUSE_OUT_OF_RANGE_START_SQL)).all()
    if not offenders:
        return
    raise RuntimeError(
        "recurrence rule(s) start outside the calendar this application "
        "reaches (2000-01-01..2100-12-31): "
        + "; ".join(
            f"id={row.id} starts_on={row.starts_on}" for row in offenders
        )
        + ".  A date near the end of the representable range overflows the "
        "pay calendar's forward projection, so such a rule cannot be resolved "
        "at all.  Correct the date and re-run."
    )


def upgrade():
    """Re-backfill, tighten the four columns, complete one CHECK and add one."""
    bind = op.get_bind()
    # The SAME refusal R7c-a made, re-asked because the schedule it grades
    # against can have moved since: an owner whose pay periods were deleted
    # between the leaves has no floor to measure an occurrence from, and the
    # backfill below would leave a NULL the tighten then reports as a bare
    # "column contains null values".
    refuse_underivable(bind)
    op.execute(sa.text(BACKFILL_SQL))
    verify_backfilled(bind)

    for column in _TIGHTENED_COLUMNS:
        op.alter_column(
            "recurrence_rules", column, nullable=False, schema="budget",
        )

    refuse_inconsistent_nominal_days(bind)
    op.drop_constraint(
        "ck_recurrence_rules_nominal_day", "recurrence_rules",
        type_="check", schema="budget",
    )
    op.create_check_constraint(
        "ck_recurrence_rules_nominal_day", "recurrence_rules",
        _NOMINAL_DAY_CHECK_COMPLETE, schema="budget",
    )

    refuse_out_of_range_starts(bind)
    op.create_check_constraint(
        "ck_recurrence_rules_starts_on_range", "recurrence_rules",
        _STARTS_ON_RANGE_CHECK, schema="budget",
    )


def downgrade():
    """Refuse: the DDL reverts cleanly but a re-``upgrade`` would not.

    **The unsafe half is :func:`upgrade`'s re-backfill, not this function's
    DDL.**  Dropping the three CHECKs and the four ``NOT NULL``s drops no value
    and rewrites no row.  What makes the pair unsafe is that Alembic's contract
    invites the reverse trip: a database sitting at this revision's parent
    (:data:`down_revision`) may be upgraded again, and by then its rules were
    written by THIS revision's write
    door, which does not write ``start_date`` or ``month_of_year`` --
    two of the three columns :data:`BACKFILL_SQL` derives ``starts_on`` from.
    Re-running it therefore re-derives every first occurrence from columns
    nothing maintains, and the result is not a NULL an operator would notice
    but a plausible earlier date: a rent authored to first occur 2026-09-15
    comes back 2024-01-15, generating 32 months of backdated rows into pay
    periods that have already closed.

    Refusing here rather than in :func:`upgrade` is what keeps the FORWARD path
    exactly as measured -- the re-backfill is correct on the only inputs it
    ever sees in the intended direction, rules written by R7c-a's door -- while
    making the direction that breaks it a deliberate act with a restore behind
    it.

    Raises:
        NotImplementedError: Always, with the SQL to loosen the schema by hand.
            The revision it re-stamps to is :data:`down_revision` itself
            rather than a literal: a literal named ``f2a94c7e1b60``, the
            parent this revision had until a 2026-08-15 merge re-pointed it,
            and an operator following it landed one revision low, where the
            next upgrade failed re-adding a column (finding plan#1902).
    """
    raise NotImplementedError(
        "Migration b6d41f0a9c27 has no safe automatic downgrade.  Loosening "
        "the schema is loss-free, but a subsequent `alembic upgrade` re-runs "
        "this revision's backfill over rules its own write door authored -- "
        "and that door writes neither start_date nor month_of_year, two of "
        "the three columns the derivation reads.  Measured: a rule authored "
        "to first occur 2026-09-15 comes back 2024-01-15.  Restoring those "
        "columns inside this function was rejected because the closed set "
        "cannot express a Monthly First cadence's day (2026-03-15 comes back "
        "2026-04-01).  REVERT THE DATA BY RESTORING THE DATABASE.  To loosen "
        "the SCHEMA alone, by hand:\n"
        "  ALTER TABLE budget.recurrence_rules\n"
        "    DROP CONSTRAINT ck_recurrence_rules_starts_on_range,\n"
        "    DROP CONSTRAINT ck_recurrence_rules_nominal_day,\n"
        "    ADD CONSTRAINT ck_recurrence_rules_nominal_day CHECK (\n"
        f"      {_NOMINAL_DAY_CHECK_R7C_A}),\n"
        "    ALTER COLUMN unit_id DROP NOT NULL,\n"
        "    ALTER COLUMN placement_id DROP NOT NULL,\n"
        "    ALTER COLUMN shift_id DROP NOT NULL,\n"
        "    ALTER COLUMN starts_on DROP NOT NULL;\n"
        f"  UPDATE alembic_version SET version_num = '{down_revision}';"
    )
