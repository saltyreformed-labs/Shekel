"""a settle carries the DAY the money moved, and the click instant is deleted

Plan step X-f1b of ``docs/audits/balance_architecture/README.md``, ruling
**R-EC** (2026-08-03).

**The column stored the wrong fact, and eleven readers corrected for it.**
``transactions.paid_at`` is ``db.func.now()`` at the moment the user clicks
"mark paid" (the status seam), and the API refused any other value.  Nothing in
the application needs that instant: an AST census of ``app/`` found **14 sites
turning it into a civil day -- 11 call sites across 8 modules** over three
helper layers, and **zero** templates, JavaScript files or serialized payloads
reading the column itself.  Nothing anywhere ordered or compared two instants.
So the app carried a precise measurement of a bookkeeping keystroke and derived
the fact it actually needed -- which day the money moved -- eleven times over.

Measured on a 2026-08-03 production clone, **65.2% of the settled Checking rows
share a click-minute with another row** (88 of 135, largest batch 6), so for two
thirds of that account the instant describes when the user sat down to do the
books, not when the bank moved money.

``settled_on`` replaces it rather than joining it (ruling R-EC).  Keeping both
would leave two columns stating one fact, which is this arc's own root cause 1
and the mirror image of the defect plan step S1-c removed one table over
(``transaction_entries.entry_date`` carrying two facts).

**No BALANCE moves the day this ships, and one soft metric does.**  The backfill is the DELETED derivation
verbatim -- ``COALESCE((paid_at AT TIME ZONE 'America/New_York')::date,
pay_period.start_date)``, which is exactly what
``app.utils.dates.to_display_civil_date`` computed at each of the eleven read
sites -- so every settled row keeps the day the engine already gave it.  The
zone is ruling R-DH (b): the derived day is compared against and bucketed by
plain ``DATE`` columns that mean the USER's civil days
(``pay_periods.start_date`` / ``end_date``), so deriving it in UTC compares two
calendars.  ``America/New_York`` is pinned as a literal here for the same reason
migration ``c4a19e7b2d80`` pins it: a migration is a historical record of what
ran, and reading a live constant would let a future config change silently
rewrite what this backfill meant.

**The one figure that DOES move is the payment-timeliness metric, and it moves
on a day nothing observed** (finding N-181).  The 8 legacy settled rows whose
``paid_at`` was NULL take their pay period's ``start_date`` here, exactly as
every reader already derived for them -- so no balance moves.  But
``Transaction.days_paid_before_due`` gated on "was an instant recorded" and now
gates on "is the row settled", so those 8 enter
``spending_analysis.payment_timeliness_from_txns`` for the first time.  Measured:
the four expense legs report 8 days early, on time, on time and 1 day late.
Narrowing this backfill to ``paid_at IS NOT NULL`` was REJECTED -- it leaves 8
settled rows undated, which the balance walk now REFUSES, trading a soft metric
for a 500 on the grid.  Recorded rather than glossed, because
``verify_balance_baseline.py`` structurally cannot see it: it is not a balance.

**The NULL is the invariant, not a gap.**  ``settled_on`` is written for every
row in a SETTLED status (Paid / Received / Settled) and left NULL for every
other, so *a row is settled if and only if it carries a settle day*.  Measured
on the same clone: **0 of 741 non-settled rows carry a ``paid_at``** and all 156
settled rows resolve a pay period, so the backfill establishes the invariant
exactly rather than approximately.  Soft-deleted rows are backfilled too --
nothing excludes them, because a restore must not resurrect a settled row with
no day.

**Why there is no CHECK.**  The invariant's predicate lives in ``ref.statuses``
(``is_settled``), and a CHECK constraint cannot join; hardcoding the three
settled ids into one would be the magic-number defect the project's own
standards forbid, and would break the moment a status is added or removed (plan
step **X-am** proposes removing one).  It is enforced STRUCTURALLY instead:
``status_seam.apply_status_change`` is the single door that writes
``status_id`` and it writes the day in the same call, so the two cannot diverge.
There is no bound on the value either -- a settle legitimately precedes its
budget period (21 of the 156 settled rows on this clone fall outside it -- 11
before its start and 10 after its end), and a "not in the
future" rule is not expressible in a CHECK because it is not immutable.

**Destructive, and the downgrade REFUSES.**  A date cannot reconstruct an
instant.  Every settled row's day is either the backfill's or a correction the
user typed, and after the drop nothing distinguishes them or re-derives either,
so ``downgrade`` raises with the literal recovery SQL rather than silently
fabricating 148 timestamps -- the shape ``.claude/rules/database.md`` prescribes
when a downgrade cannot be faithful.  Recorded honestly rather than glossed:
``system.audit_log`` holds a ``paid_at`` for only **106 of the 148** rows (its
retention window starts 2026-05-06), so the reassuring "the audit trail has it"
is false for 42 of them and those instants are lost for good.

**A rollback of the deploy carrying this migration is NOT a pure digest
revert.**  The schema moves forward and the previous image's code selects
``paid_at``; reverting the image alone leaves it querying a dropped column.
Rolling back means running this ``downgrade`` (accepting its refusal and the
recovery path) or restoring from backup.  Stated here because this arc's ship
notes assert the opposite for migration-free ships and the distinction must not
be assumed.

Review: developer, 2026-08-03 (ruling R-EC, taken on the "which option is what I
should do if I were building everything from scratch" framing, with the
destructive migration, the always-refusing downgrade, and the permanent loss of
42 instants all stated before the ruling was taken).

Revision ID: a3f7c8e21b64
Revises: d7c1f4a9e603
Create Date: 2026-08-03 15:30:00.000000
"""
import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic.
revision = 'a3f7c8e21b64'
down_revision = 'd7c1f4a9e603'
branch_labels = None
depends_on = None


# The deleted derivation, verbatim.  ``to_display_civil_date(paid_at,
# pay_period.start_date)`` is a display-timezone civil day with the period start
# as the NULL fallback, applied UNCONVERTED because that fallback is already a
# civil date -- routing it through a zone would shift it a day earlier and, on
# this arc's own measurement, move 3 of 4 such rows into the previous pay
# period.  ``AT TIME ZONE`` on a ``timestamptz`` yields the local wall clock, so
# the ``::date`` below is the user's day; the NULL arm never reaches it.
_BACKFILL = sa.text("""
    UPDATE budget.transactions t
    SET settled_on = COALESCE(
        (t.paid_at AT TIME ZONE 'America/New_York')::date,
        pp.start_date
    )
    FROM budget.pay_periods pp, ref.statuses s
    WHERE pp.id = t.pay_period_id
      AND s.id = t.status_id
      AND s.is_settled
""")

# The post-backfill gate: a settled row with no day would be a row the balance
# walk must fold and cannot date, so it fails the migration rather than shipping
# a fold that guesses.
_UNDATED_SETTLED = sa.text("""
    SELECT t.id, t.name, t.status_id, t.pay_period_id
    FROM budget.transactions t
    JOIN ref.statuses s ON s.id = t.status_id
    WHERE s.is_settled AND t.settled_on IS NULL
    ORDER BY t.id
""")

# The other half of the invariant, checked in the same pass: a row that is NOT
# settled must carry no day.  The backfill cannot produce one (its WHERE
# narrows to settled statuses), so a hit here means the column arrived
# non-empty, which would mean this migration ran twice against a mutated
# schema.
_DATED_UNSETTLED = sa.text("""
    SELECT t.id, t.name, t.status_id, t.settled_on
    FROM budget.transactions t
    JOIN ref.statuses s ON s.id = t.status_id
    WHERE NOT s.is_settled AND t.settled_on IS NOT NULL
    ORDER BY t.id
""")


# The third gate, and the one the other two did not cover: a transfer's two
# shadows must derive the SAME day.  ``posting_service._entry_date`` reads the
# INCOME shadow's day for the pair and its docstring rests on the two being
# equal (Transfer Invariant 3), so a divergent pair would date one leg of a
# balanced entry from a day the other leg does not share.  The backfill cannot
# CREATE a divergence -- both shadows carry the same ``pay_period_id`` and, in
# practice, the same ``paid_at`` -- but "cannot" was an assumption while the
# other two invariants were gated, so it is measured instead.  Measured on the
# 2026-08-03 production clone: 0 transfers diverge.
_DIVERGENT_SHADOW_PAIR = sa.text("""
    SELECT t.transfer_id,
           MIN(t.settled_on) AS earliest,
           MAX(t.settled_on) AS latest
    FROM budget.transactions t
    JOIN ref.statuses s ON s.id = t.status_id
    WHERE t.transfer_id IS NOT NULL
      AND s.is_settled
    GROUP BY t.transfer_id
    HAVING MIN(t.settled_on) IS DISTINCT FROM MAX(t.settled_on)
    ORDER BY t.transfer_id
""")


def upgrade():
    """Add the settle day, backfill it from the deleted derivation, drop the instant."""
    op.add_column(
        "transactions",
        sa.Column("settled_on", sa.Date(), nullable=True),
        schema="budget",
    )

    connection = op.get_bind()
    connection.execute(_BACKFILL)

    undated = connection.execute(_UNDATED_SETTLED).fetchall()
    if undated:
        listed = "; ".join(
            f"id={row[0]} name={row[1]!r} status_id={row[2]} "
            f"pay_period_id={row[3]}"
            for row in undated
        )
        raise RuntimeError(
            f"Refusing to drop budget.transactions.paid_at: {len(undated)} "
            f"settled row(s) have no settled_on after the backfill -- "
            f"{listed}.  Every settled row must carry the day its money moved, "
            "because the balance walk folds it and has nothing else to date it "
            "by once paid_at is gone.  Diagnose with: SELECT t.id, t.paid_at, "
            "t.pay_period_id FROM budget.transactions t JOIN ref.statuses s ON "
            "s.id = t.status_id WHERE s.is_settled AND t.settled_on IS NULL;"
        )

    dated_unsettled = connection.execute(_DATED_UNSETTLED).fetchall()
    if dated_unsettled:
        listed = "; ".join(
            f"id={row[0]} name={row[1]!r} status_id={row[2]} "
            f"settled_on={row[3]}"
            for row in dated_unsettled
        )
        raise RuntimeError(
            f"Refusing to drop budget.transactions.paid_at: {len(dated_unsettled)} "
            f"NON-settled row(s) already carry a settled_on -- {listed}.  The "
            "backfill above writes only settled rows, so this means the column "
            "was populated by something else and the settled-iff-dated "
            "invariant this migration establishes does not hold."
        )

    divergent = connection.execute(_DIVERGENT_SHADOW_PAIR).fetchall()
    if divergent:
        listed = "; ".join(
            f"transfer_id={row[0]} earliest={row[1]} latest={row[2]}"
            for row in divergent
        )
        raise RuntimeError(
            f"Refusing to drop budget.transactions.paid_at: "
            f"{len(divergent)} settled transfer(s) have shadows whose "
            f"settled_on disagree -- {listed}.  Transfer Invariant 3 says the "
            "two shadows carry one day, and posting_service._entry_date dates "
            "the PAIR's journal entry from the income shadow alone, so a "
            "divergent pair would file one leg of a balanced entry on a day "
            "the other leg does not share."
        )

    op.drop_column("transactions", "paid_at", schema="budget")


def downgrade():
    """Refuse: a civil date cannot reconstruct the instant this dropped.

    ``NotImplementedError`` rather than ``RuntimeError``: it is the class
    ``.claude/rules/database.md`` names for a downgrade that cannot be
    faithful, and the three existing unconditional refusals in this tree use
    it (``7abcbf372fff``, ``b4c5d6e7f8a9``, ``a80c3447c153``).  A
    ``RuntimeError`` here is reserved for a conditional fail-loud guard
    inside a WORKING downgrade.

    See the module docstring.  The refusal is unconditional because after the
    upgrade nothing distinguishes a backfilled day from one the user typed, and
    nothing re-derives either -- so any automatic reconstruction would fabricate
    148 timestamps and silently change which rows the timeliness analytics
    consider knowable (it gates on ``paid_at IS NOT NULL``, and 8 rows carry a
    day that came from their pay-period start rather than from any instant).

    The revision the recovery SQL re-stamps to is :data:`down_revision` itself
    rather than a literal, so it cannot name a parent this revision no longer
    has: migration ``b6d41f0a9c27``'s literal did, after a merge re-pointed it
    (finding plan#1902).
    """
    raise NotImplementedError(
        "Cannot downgrade a3f7c8e21b64: budget.transactions.settled_on holds a "
        "civil DAY and budget.transactions.paid_at held an INSTANT, so nothing "
        "here reconstructs the dropped value -- and a settled row's day may be "
        "a correction the user typed off a bank statement rather than the "
        "original derivation.  To downgrade anyway, accepting that every "
        "restored instant is a fabrication that keeps only the DAY correct "
        "(which is all any reader ever consumed), run by hand:\n"
        "\n"
        "  ALTER TABLE budget.transactions\n"
        "      ADD COLUMN paid_at TIMESTAMP WITH TIME ZONE;\n"
        "  UPDATE budget.transactions t\n"
        "     SET paid_at = (t.settled_on + TIME '12:00')\n"
        "                   AT TIME ZONE 'America/New_York'\n"
        "   FROM ref.statuses s\n"
        "  WHERE s.id = t.status_id AND s.is_settled\n"
        "    AND t.settled_on IS NOT NULL;\n"
        "  ALTER TABLE budget.transactions DROP COLUMN settled_on;\n"
        f"  UPDATE alembic_version SET version_num = '{down_revision}';\n"
        "\n"
        "Noon is chosen so the restored instant round-trips to the same civil "
        "day in America/New_York on both sides of every DST transition.  Rows "
        "whose original paid_at was NULL (a historical settle) will come back "
        "non-NULL, which widens the payment-timeliness analytics by those rows."
    )
