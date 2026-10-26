> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# The pay calendar, as built: the C3 writer span (2026-08-16)

**Read-only history. Nothing here governs anything.** The live document is
`docs/plans/implementation_plan_pay_calendar.md`; this record exists so that document's shipped
entries can be pointers rather than accounts of themselves (`conventions.md` rule 5). Archived to
make room for `C2-f2d`'s three leaf specifications, under the same rule that says to shrink the
record of what is DONE and never the specification of what remains.

**`C3` is a COMPLETED span**: both leaves shipped, its container ticked with the last of them, and
no live step depends on a sentence that lived only here. What the code does now is stated by
`pay_period_write`'s own module docstring, which declares itself the one place in `app/` that
constructs or deletes a pay period.

## The three steps

- **C3 -- the writer writes paydays, forward-only.** `7e3fb33b`. The DECOMPOSED parent; ticked with
  `C3-b`, its last leaf.

- **C3-a -- the destructive form stops keying on an ordinal.** `5f1e2bd6`.
  `keep_through_period_id`, a `RowId` resolved against the owner's own periods; anything else is
  `PayPeriodUnresolved`, with both F-144 branches logged. The tail is selected by PAYDAY, so no part
  of the operation reads a column `C4` drops. The lock classifier moved to `pay_period_locks`
  (developer ruling). Closed **P13**; opened **P29**, **P30**.

- **C3-b -- the writer materialises the derivation.** `7e3fb33b`. `pay_period_write` is the one
  place in `app/` that constructs or deletes a pay period, writing `derive_periods` over the WHOLE
  payday list every time. R-PC1's floor is one full CADENCE; its coverage half is DELETED (developer
  2026-08-11: a stranded settle day cancels on both sides of R-K, and `integrity_check` BA-06 asks
  it as a query). Closed **P2**'s writer half, **P12**, **P29**, **N-127**; opened **P31**, **P32**;
  found **P33**.

## What a LATER step must still obey

Two of these are cited by live work and are repeated in the live document's rulings table, so this
archive is not load-bearing for either:

- **The forward-only FLOOR is one full cadence after the latest payday**, and its whole job is
  keeping `C6` closed; `C6` deletes it. The COVERAGE half of R-PC1 is deleted and must not be
  reinstated -- the claim it rested on (that stranding a settled day reproduces `balance:N-128`) was
  measured false.
- **`pay_period_write` is the ONE writer.** `C2-f3` moves `pay_period_admin`'s three write-path
  ORM-row reads into it rather than beside it, and `C4` changes that one file plus the readers.

**P33** remains OPEN and is carried on `ledger.md`, not here.

## P38, closed at C4-a-1 (`8962e073`, 2026-08-27)

**The finding.** `_cash_fold` sampled a column at its DERIVED end while `_cash_plan`, one module
over, clamped a projected row against the STORED span it read off `txn.pay_period` -- one module,
two ends. Its stated MECHANISM was refuted the day it was written (ruling R-K's identity does NOT
break, because the fold's steps and the period view's grouping read the same `day_nets`); what
survived is that a row could RENDER in a column its budget period is not, reported as
`period_timing`.

**Why it closed here rather than at `C4-c`, which owned it.** The row listed three sites and two had
already moved: `grid/_mobile_plan.html` renders a `PeriodWindow` and
`savings_dashboard_service._net_worth` a `list[DerivedPeriod]`, both since `C2-f2`. All three were
re-grepped against the row's OWN predicate before it was closed, not just the site this step
touched.

**What it cost.** `$0.00`, measured on production and on both dev clones: 62 periods, 0 stored ends
disagreeing with the derivation, 0 of 699 still-projected rows whose landing day moves.
`tests/manual/verify_c4a1_cash_fold_equality.py` produces byte-identical documents on the branch and
on its merge base against one clone, and planting one corrupted stored end makes those two arms
disagree on 9 rows -- so the equality is a measurement rather than a blind instrument.

**Its pin moved and was renamed.** The row cited
`test_cash_period_view.test_a_PROJECTED_row_clamped_against_the_stored_span_still_reconciles`, which
pinned the refutation while the split existed. It is now
`test_a_corrupted_stored_end_moves_no_PROJECTED_figure_either`, asserting the opposite figures: the
row lands INSIDE the column that budgeted it, so `period_timing` is `$0.00` where it was `+$250.00`.
