> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# pay_calendar C4's reader census, as built (archived 2026-09-02)

Archived out of `implementation_plan_pay_calendar.md` when `C4` ticked, on the
developer's ruling that an as-built is unnecessary where it survives in the committed
code. `C4` shipped at **`327a70f2`**; its leaves' own commits are the record.

**THE READER CENSUS was the leaf list and the leaves have taken it**; its three re-measurements
(2026-08-25 by AST, 2026-08-28 at `C4-a-2` for a SHAPE the first was blind to, and `C4-a-5`'s, which
found a reader in `tests/` that both a paragraph and a fresh grep missed) are condensed here under
rule 5, the commits being the record.
**What survives is the PREDICATE, because a predicate cannot go stale where a count can**, and
`C4-c` re-runs it rather than trusting any list: *a read of `end_date` or `period_index` reached
through a `budget.pay_periods` ROW -- in query position, through `.pay_period`, or through a local
bound from it.* `pay_schedule_service.resolve_schedule` is `C4-b-2`'s. Each `C4-a` leaf's "what a
later leaf must obey" has MOVED into `C4-c`'s specification below, which is where rule 4 sends an
entry's overflow; nothing live depends on a line here.
