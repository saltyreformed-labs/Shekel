> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# recurrence:R13 as built (2026-09-13)

**A DAY-OF-MONTH pay schedule.** `R13`'s entry in `implementation_plan_recurrence_redesign.md` as it
stood the moment it shipped in one commit with `pay_calendar:C17-d-2` (`d79986c7`, migration
`3ec5291ca4e2`) -- moved here under conventions.md rule 5 because that document stood at 879 of
900 lines, inside the gate's 20-line headroom, and a ticked entry holds six. The `[ ]` below is `[x]`
at that hash; the specification it names is `implementation_plan_pay_calendar.md`, section 4.

## The entry, verbatim

- [ ] **R13 -- a DAY-OF-MONTH pay schedule** (rulings **R-R28**, **pay_calendar:R-PC68**). ONE
      commit with `pay_calendar:C17-d`, whose specification (`implementation_plan_pay_calendar.md`,
      section 4) is this step's; it ticks with that leaf and shares its rank.

Semi-monthly pay is the 1st and the 15th (or the 15th and the last day), and a fixed-length walk is
not: `round(365.2425 / 15) = 24` gives an owner the right COUNT with paydays that drift through the
month -- Jan 1, Jan 16, Jan 31, Feb 15. **Monthly already carries the identical limitation** (a
30-day walk is not "the 1st"), and pay-calendar finding **F-4** records that `pay_periods` stores
NOMINAL paydays generally, so this is one shape rather than a semi-monthly special case.

Since `pay_calendar:C17-a` (`6caf56bc`) the schedule is a SEQUENCE OF ERAS and every era already
carries a `kind_id` into `ref.pay_cadence_kinds`, holding its one member `fixed_days`. What this
step adds is the `monthly` and `semi_monthly` members and the branch on the era's kind in the ONE
arithmetic body `pay_calendar:C14-d` made of the grid -- `_grid.nominal_payday` and
`cadence_steps_to` -- plus `PayCadence.periods_per_year` answering 24 and 12 without dividing. The
batch writer and the derivation are not branched separately: both reach the grid. Re-derived cost of
not having it (R-PC68): a semi-monthly owner on a 15-day walk is modelled `24.35` paychecks a year
against a true 24. It was ranked last in this arc until 2026-09-11 because it edits the pay-calendar
package's core; that package is now where it is filed.

