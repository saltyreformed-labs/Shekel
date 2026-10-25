> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# pay_calendar:C17-d as built (2026-09-14)

**The day-of-month cadence KIND.** The `C17-d` span of `implementation_plan_pay_calendar.md` as it
stood after `C17-d-3` (`4420df87`) shipped the container -- moved here under conventions.md rule 5 as
a COMPLETED span when that document stood at 498 of 520 lines against the gate's 20-line headroom
arm and `C20-a`'s tick needed room. Every entry below was already ticked; the leaves' full records are
their commit messages (`7a6bbaf5`, `d79986c7`, `4420df87`) and PRs #360, #366, #370.

## The span, verbatim

- [x] **C17-d -- the day-of-month cadence KIND.** `4420df87` (**R-PC68**; one commit with
      `recurrence:R13` at `C17-d-2`). The DECOMPOSED parent, split 2026-09-13 (**R-PC81**) into the
      value's shape, the kinds with their migration, and the forms; ticked with `C17-d-3`. MOVED
      MONEY at `C17-d-2` for a month-kind owner; `$0.00` on production.
- [x] **C17-d-1 -- the cadence is a value of its own kind.** `7a6bbaf5`. `pay_rhythm.FixedDays`;
      `Rhythm.cadence`; `Era` without `kind` (the type is the kind, **R-PC80**); `_grid` dispatches
      on the value's type through one table; `PayCadence` takes the value; `era_to_mint` asks the
      grid's round trip. `$0.00`, a pure refactor.
- [x] **C17-d-2 -- the `Monthly` and `SemiMonthly` kinds.** `d79986c7`. Month arithmetic in `_grid`
      (a day 29..31 clamps to the month's end); migration `3ec5291ca4e2`: `cadence_days` nullable,
      `nominal_day` and the other day with CHECKs, `kind_id` and `ref.pay_cadence_kinds` DROPPED
      (**R-PC80**); `PayCadence` 12 / 24 without dividing; the event's phrase (**R-PC82**),
      registration's day (**R-PC83**); P78's eight fixtures through the door. MOVED MONEY for such
      an owner, `$0.00` on production. Closed **P78**; N-399 re-measured, re-homed as **SAL-556**.
- [x] **C17-d-3 -- the doors offer the kind.** `4420df87`. Three RADIO ARMS, no script, distinct
      wire keys per arm; the schema's `@post_load` hands back the `Rhythm`; `rhythm_to_wire` the one
      inverse; one macro on all four doors (**R-PC84**). The rhythm's form moved to
      `schemas/validation/_pay_rhythm.py` (the doors' module crossed 1,000 lines). `add_months`
      folded onto the clamp; the event's description and the last dropped-column lines put right.
      Closed **PC-508**, **PC-513**, **PC-514**.
