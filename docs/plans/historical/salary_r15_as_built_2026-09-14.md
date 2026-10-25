> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# salary:R15 as built (2026-09-14)

**What a payroll deduction's own FREQUENCY means: a recurrence rule on the row.** The `R15` span of
`implementation_plan_salary.md` as it stood the moment `R15-c` (`77901fe0`) shipped and the
container ticked with it -- moved here under conventions.md rule 5 as a COMPLETED span (the document
stood at 298 of 320 lines, two under the gate's 20-line headroom arm). The `[ ]` entries below (`R15`, `R15-b`,
`R15-c`) are `[x]` at `4ed9b5b3` (R15-b) and `77901fe0` (R15-c); the leaves' full records are
their commit messages (`4ed9b5b3`, `77901fe0`) and `~/projects/shekel-handoffs/HANDOFF-salary-R15.md`
sections 7 and 9. R15-b's open question (how a NEW deduction states its cadence between the two
leaves) was answered by **R-SAL35**: one PR for both, so the form was never deployed without its
cadence control.

## The span, verbatim

- [ ] **R15 -- what a payroll deduction's own FREQUENCY means** (**R-SAL3**; findings **F-21**,
      **SAL-549**; **N-395** left at R15-a's tick, its fix having shipped at `R14-b` `e0f0c05f`).
      `deductions_per_year` stores 26 / 24 / 12 as a three-valued MODE the engine only compares
      against; 11 of the developer's 12 live deductions carry 24, a cadence the recurrence
      vocabulary could not say. The DECOMPOSED parent, split into THREE leaves 2026-09-13 once its
      four forks were ruled (**R-SAL29**-**R-SAL32**); ships with `R15-c`.
- [x] **R15-a** `bdd77055` -- the per-month CEILING as the cadence's third value (**R-SAL29** as
      amended: a paycheck cadence counts the month's paydays on the OWNER'S calendar; a week cadence
      its own first N): `budget.recurrence_rules.max_per_month` + CHECK (migration `ef32dfe4cd8e`),
      `_ceilinged` between the walk and the bound, `monthly_equivalent` off the aggregator, the
      template form end to end; three pure moves for the line ceiling. NO FIGURE MOVED; the browser
      drive was run by the developer.
- [ ] **R15-b** -- the third owning arm `paycheck_deduction_id` (an exactly-one-of-three CHECK),
      `PayrollBasis` resolving each line's rule ONCE and the engine asking it whether a payday is an
      admitted occurrence, and the migration writing one rule per 24 / 12 line (`starts_on` = the
      owner's opening payday, **R-SAL30**) then DROPPING `deductions_per_year` and
      `_deduction_applies_at`; graded byte-identical over the 63 saved paychecks. **How a NEW
      deduction states its cadence between this leaf and `R15-c` -- or whether the two ship in one
      PR -- is a question for the developer before it is built.** Closes **F-21**, **SAL-549**;
      carries **SAL-556** (ex N-399). A migration; own review.
- [ ] **R15-c** -- the deduction form takes `_recurrence_fields.html` (**R-SAL31**), the end-bound
      and due-day rows off by flag and the ceiling on, replacing the 26 / 24 / 12 select and
      `app.js`'s prefill.
