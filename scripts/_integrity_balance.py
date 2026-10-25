"""
Shekel Budget App -- Data Integrity Check: the balance family (BA-*)

One of the four family modules ``scripts/integrity_check.py`` runs, split
along its ``--category`` values at plan step ``balance:X-bi-6-4c-4`` (ruling
**R-BAL161**); ``scripts/_integrity_core.py`` says why.
"""

from scripts._integrity_core import (
    TRANSFER_LEG_RECORDS_JOIN,
    CheckSpec,
    run_check,
)


# ── Category 3: Balance Anomalies ────────────────────────────────


def check_balance_anomalies(session):
    """Run all BA-* balance anomaly checks.

    Flags potential issues in the anchor balance and projection system.

    Args:
        session: SQLAlchemy session.

    **Severity is per CHECK, not per family** (plan step X-f1c3c).  It was one
    ``"warning"`` constant applied to the whole list, which was true of every
    member while they were all anchor-cache smells; re-pointing BA-01 at "this
    account has no balance assertion at all" made it false, because that state
    makes ``cash_ledger.resolve_anchor`` raise on every page that renders the
    account.  A family constant silently downgraded it, so the sweep exited 2
    and ``verify_backup.sh`` logged a WARNING for a broken restore.  The
    per-check form is the one ``check_data_consistency`` already uses, so this
    is the file's own established shape rather than a new one.

    **THREE members were deleted at plan step ``pay_calendar:C4-c``, and none of
    them was replaced.**  BA-03 (an ordinal GAP), BA-04 (a date OVERLAP) and
    BA-07 (a day covered by no pay period) were three faces of one defect --
    ``budget.pay_periods`` storing ``end_date`` and ``period_index`` beside the
    paydays they derive from, with nothing reconciling them.  That step dropped
    both columns, so a period's ordinal is its position in payday order and its
    end is the day before the next payday: an ordinal cannot gap, two periods
    derived from distinct sorted paydays cannot overlap, and consecutive
    intervals leave no uncovered day between them.  A query for a state the
    schema cannot express is not a check, it is a query that always returns
    nothing.

    **What BA-07 detected, said precisely, because an adversarial review of
    C4-c found this paragraph claiming more than it should** (2026-09-01).  Its
    subject was a stored ``end_date`` FALLING SHORT of the next payday -- a
    hole between two adjacent periods.  It was never a detector for IRREGULAR
    PAYDAY SPACING: on any schedule the writer produced, the stored end already
    equalled ``lead(start) - 1``, so a payday six months after its predecessor
    passed BA-07 before the drop exactly as it passes now.  That state is still
    constructible (``/pay-periods/generate`` accepts any payday on or after the
    floor) and still unobserved by this sweep, where it presents as one
    over-long paycheck rather than as an absent day.  It is recorded as a
    finding rather than closed here silently: seeing it needs a NEW predicate
    (``next_payday - payday > cadence_days``), which is a check this sweep has
    never had and not one C4-c removed.

    Returns:
        List of CheckResult for the surviving balance/anchor checks
        (BA-01 critical; BA-05, BA-06 warnings.  BA-02 was deleted with the
        anchor cache columns at plan step X-f1c3c; BA-03, BA-04 and BA-07 with
        the derived columns at ``pay_calendar:C4-c``).
    """
    checks = [
        # BA-01 and BA-02 both keyed on ``accounts.current_anchor_*``, deleted
        # at plan step X-f1c3c (ruling R-EH): BA-01 looked for one of the pair
        # set without the other, and BA-02 for an anchor period past the end of
        # the user's schedule.  Neither state is expressible now.  BA-01 is
        # RE-POINTED at the invariant those columns existed to serve -- every
        # account carries at least one balance ASSERTION (E-19 / Commit 3) --
        # because an account the resolver cannot answer for is the state that
        # actually breaks every producer downstream.  BA-02 is deleted with no
        # replacement: an assertion carries a DAY, and a day outside the
        # schedule is legitimate (money moved before you started budgeting).
        # CRITICAL, unlike its siblings: an account with no assertion is not an
        # anomaly to look at later, it is an account ``resolve_anchor`` raises
        # for -- the balance engine has no starting point, so every producer
        # downstream of it fails.  BA-05 and BA-06 flag states worth a human's
        # attention that still render.
        # BA-01 asks about the OWNER's assertions: since plan step
        # balance:X-bj-1 the same table holds the bank's statement placements,
        # and ``resolve_anchor`` reads the owner's rows alone until the flip
        # (``balance_predicates.owner_declared_clause``; this raw SQL is that
        # predicate's one second spelling, named there).  An account whose
        # only level is a bank's is an account the resolver still raises for.
        ("BA-01", "critical", "Accounts with no balance assertion at all", """
            SELECT a.id, a.name
            FROM budget.accounts a
            LEFT JOIN budget.account_anchor_history h
                   ON h.account_id = a.id
                  AND h.statement_import_id IS NULL
            WHERE h.id IS NULL
        """),
        # BA-06 is a CHECK and deliberately not a refusal or a log line
        # (developer ruling 2026-08-11, which deleted pay_calendar C3-b's
        # coverage rule).  That rule refused any schedule write leaving a
        # settled row's cash day outside every paycheck, on the claim that it
        # breaks ruling R-K's reconciliation identity -- and it does not: each
        # column is valued at its OWN ``end_date``, so the day is absent from
        # both sides and reports as the ``period_timing`` remainder.  Nothing
        # is WRONG here, which is why this is a warning rather than a gate.
        #
        # It lives here rather than in the writer for the reason the arc keeps
        # finding: the condition is DERIVABLE from the schedule and the row's
        # own settle day, so recording it at write time would store a computed
        # claim beside no reconciler -- the same defect ``pay_calendar:C4-c``
        # removed by dropping ``end_date`` -- and it would go stale on the next
        # write.  Asked as a query it is always current, covers every owner,
        # and reports the state however it arose, including from data no writer
        # produced.
        #
        # **The predicate became a RANGE test at C4-c, and the collapse is the
        # normalization rather than a rewrite.**  It used to ask
        # ``NOT EXISTS (a period whose stored span contains the day)``, because
        # a stored ``end_date`` could fall short of the next payday and leave an
        # interior hole a settle day could land in.  Derived, the periods TILE:
        # each ends the day before the next opens, so the only days outside
        # every paycheck are the ones before the first payday and after the
        # horizon.  Two comparisons say that, and BA-07 -- which existed to see
        # the interior hole this sub-select was also catching -- is deleted.
        #
        # The horizon is ``MAX(start_date) + (cadence_days - 1)``, which is the
        # derivation's own projected end for the last period.  The cadence is
        # the LATEST ERA's since plan step ``pay_calendar:C17-a``: the
        # schedule row no longer carries one, and the last period's end reads
        # the latest era exactly as ``pay_schedule_service.ScheduleFacts.rhythm``
        # does.  An owner who holds a payday holds an era -- every batch that
        # records one mints an era when none covers it, and the C17-a
        # migration backfills one per owner -- so the join drops nobody.
        # *This SQL restates the derivation's end rule a second time, and the
        # arithmetic form here ignores the payday convention; that it should
        # be DELETED rather than made exact is the fork ledger row PC-501
        # carries for the developer, and re-pointing the join is not a ruling
        # on it.*
        #
        # Soft-deleted rows are excluded: they contribute to no figure on any
        # surface.  An owner with NO periods is excluded by the join rather
        # than reported as one giant violation.
        #
        # **A settled TRANSFER is graded by its LEGS since plan step
        # ``balance:X-bi-6-4c-4``** (ruling **R-BAL160**), the shape DC-11's
        # leg arm had from ``X-bi-6-4a`` until plan step
        # ``balance:X-bi-6-4d-2`` deleted it: one row per leg naming the
        # transfer and that leg's account, its day the leg's own movement's
        # (ruling **R-BAL80**: the money is the movement), asked under the
        # TRANSFER's status and ``is_deleted``.  The row arm dropped the
        # shadows (``transfer_id IS NULL``), which it used to count as rows
        # by their own day; since plan step ``balance:X-bi-6-4d-2`` no row
        # names a transfer.  The leg arm grades a MOVEMENT's day, so a side closed
        # at ``$0.00``, which holds no movement, has no day to grade and is
        # not listed -- where a ``$0.00``-closed ROW keeps the row day this
        # arm's twin reads.  On the 2026-09-30 00:11 production dump no
        # settled shadow lacked its movement or differed from its day, and no
        # shadow's status or ``is_deleted`` differed from its transfer's, so
        # the change listed nothing new there.  The schedule bounds are a CTE
        # because both arms read them.
        ("BA-06", "warning",
         "Settled transactions and transfer legs whose settle day no pay "
         "period covers", f"""
            WITH sched AS (
                SELECT pp.user_id,
                       MIN(pp.start_date) AS first_day,
                       MAX(pp.start_date) + (era.cadence_days - 1) AS last_day
                FROM budget.pay_periods pp
                JOIN (
                    SELECT DISTINCT ON (user_id) user_id, cadence_days
                    FROM budget.pay_eras
                    ORDER BY user_id, effective_from DESC
                ) era ON era.user_id = pp.user_id
                GROUP BY pp.user_id, era.cadence_days
            )
            SELECT t.id AS transaction_id, NULL::integer AS transfer_id,
                   t.account_id, p.user_id, t.settled_on,
                   sched.first_day, sched.last_day
            FROM budget.transactions t
            JOIN budget.pay_periods p ON p.id = t.pay_period_id
            JOIN ref.statuses s ON s.id = t.status_id
            JOIN sched ON sched.user_id = p.user_id
            WHERE t.is_deleted = FALSE
              AND t.transfer_id IS NULL
              AND s.is_settled = TRUE
              AND t.settled_on IS NOT NULL
              AND (t.settled_on < sched.first_day
                   OR t.settled_on > sched.last_day)
            UNION ALL
            SELECT NULL::integer, x.id, e.account_id, x.user_id, e.settled_on,
                   sched.first_day, sched.last_day
            FROM budget.transfers x
            JOIN ref.statuses s ON s.id = x.status_id{TRANSFER_LEG_RECORDS_JOIN}
            JOIN sched ON sched.user_id = x.user_id
            WHERE x.is_deleted = FALSE
              AND s.is_settled = TRUE
              AND e.settled_on IS NOT NULL
              AND (e.settled_on < sched.first_day
                   OR e.settled_on > sched.last_day)
            ORDER BY transaction_id NULLS LAST, transfer_id, account_id
        """),
        ("BA-05", "warning",
         "Large anchor balance jumps (>50% change between consecutive entries)",
         """
            WITH ordered AS (
                SELECT id, account_id, anchor_balance,
                       LAG(anchor_balance) OVER (
                           PARTITION BY account_id ORDER BY created_at
                       ) AS prev_balance
                FROM budget.account_anchor_history
            )
            SELECT id, account_id, prev_balance, anchor_balance
            FROM ordered
            WHERE prev_balance IS NOT NULL
              AND prev_balance != 0
              AND ABS(anchor_balance - prev_balance) / ABS(prev_balance) > 0.5
        """),
    ]
    return [
        run_check(session, CheckSpec(cid, "balance", severity, desc, sql))
        for cid, severity, desc, sql in checks
    ]
