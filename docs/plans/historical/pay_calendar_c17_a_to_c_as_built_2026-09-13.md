> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# `pay_calendar:C17-a` to `C17-c-2b` as built

**This is the archived specification span for the eight shipped leaves of `C17` that precede
`C17-d` -- `C17-a`, `C17-b-1`, `C17-b-2`, `C17-c`, `C17-c-1`, `C17-c-2`, `C17-c-2a`, `C17-c-2b` --
moved out of `docs/plans/implementation_plan_pay_calendar.md` on 2026-09-13 under `conventions.md`
rule 5** when `C17-d` was decomposed into three leaves (**R-PC81**) and the document, at 500 of an
effective 500 lines, had no room to specify them. Cite it for HOW a decision came to be, never for
what is true now: the code as committed is the source of truth, every ruling these leaves took is
a `rulings.md` row (**R-PC67**, **R-PC71**-**R-PC78**), and every finding they closed left
`ledger.md`.

## The entries as they stood

- [x] **C17-a -- the relation.** `6caf56bc`, migration `6fc77e86d76f`. One era per owner backfilled,
      phased on the record's opening; the three columns dropped; every reader takes the LATEST era's
      rhythm where it took the row's (`$0.00`). A batch mints an era only where it STATES a rhythm
      the covering era does not hold, and retires every era past the surviving record. Narrowed
      **N-494** to the one top-up that restates an era; closed N-492's write half. Left
      `pay_period_write.py` at 1,000 of 1,000 (**PC-507**).
- [x] **C17-b-1 -- the forward continuation leaves `_derive.py`.** `1ae0cd02`. A pure move of
      `project_period_after` and `covering_projection` into `pay_calendar/_projection.py`, between
      `_derive` and `_searches`. Closed **PC-498**.
- [x] **C17-b-2 -- readers anchor on the era's phase.** `3369b2ab` (the `_eras.py` cut, **R-PC73**)
      + `06fc0d33`. **MOVED MONEY**, `$0.00` on production's 64 rows. Piecewise projection and the
      matching rule (**R-PC72**); a minting batch bounded at its era's first payday and the era rule
      keyed on it (ruled 2026-09-11 on the review; **PC-510** born and closed: a holiday-minted
      `prior` era was re-minted a fortnight late by the next extend). Closed **N-495**, **PC-502**,
      **N-492**, **PC-505**; carried **N-496**; opened **PC-509** for `C17-c-2`.
- [x] **C17-c -- the doors ask for the ERA.** `36c6b6af`. The DECOMPOSED parent, split 2026-09-11
      (**R-PC71**) into the pure-move split of `pay_period_write.py` and the door rewrite it made
      room for; ticked with `C17-c-2b`.
- [x] **C17-c-1 -- `pay_period_write.py` leaves the ceiling.** `7d26ec2c` (+ `ddbbe87b`). The batch
      shape moved whole into `pay_period_batch.py` (**R-PC74**); the writer 998 -> 663, `$0.00`.
      Closed **PC-507**.
- [x] **C17-c-2 -- the doors.** `36c6b6af`. **R-PC64**, **R-PC67**; the DECOMPOSED parent, split
      2026-09-12 (**R-PC78**) into the `$0.00` leaf and the money leaf; ticked with `C17-c-2b`.
- [x] **C17-c-2a -- a hole is refused, and the doors ask for the scheduled day.** `3d635e4a`. A
      batch whose first new payday skips a whole paycheck of the owner's plan is REFUSED from the
      one writer (`pay_period_batch.reject_skipped_paycheck` beside the floor; **R-PC67**,
      **R-PC76**); `reject_unconfirmed_gap`, `PayPeriodGapRequired`, `confirm_gap` and the banner
      DELETED; the four payday doors ask for the SCHEDULED day. R-PC70's loud-read-path premise
      measured FALSE (**R-PC77**). Closed **P80**, **PC-504**; **N-493** re-pointed at `C17-e`.
- [x] **C17-c-2b -- extend materialises the plan, and the seam is the door's.** `36c6b6af`.
      **MOVED MONEY**, `$0.00` on production. `pay_period_write.continue_paydays` records a prefix
      of `planned_paydays_after`, minting and retiring nothing, so both fences hold by identity;
      `last_step_of` is the step before the old era's last planned payday at or before the next
      era's first (**R-PC75**), `validate_eras` refuses an era that would pay nothing, and the
      projection clamps its estimate to the seam. Closed **PC-509**, **N-494**.
