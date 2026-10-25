> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# salary:R14 span as built (archived 2026-09-11)

The three entries below are the `implementation_plan_salary.md` section 4 bodies of the shipped
`R14` span, moved here under conventions rule 5 when `salary:S3-e-2`'s registry pass needed the
lines (the document stood at 299 of its 320-line cap; the developer ruled on 2026-09-06 that the
next lane into the file archives this span rather than asking again). Each step keeps its one-line
checkbox in the live document; nothing here is re-verified.

- [x] **R14** `e0f0c05f` -- the DECOMPOSED parent of what a payroll deduction's gross is priced
      from, split 2026-09-03 (**R-SAL6**) into the EXPAND and the MONEY. Both leaves have shipped,
      so the container ships with the last of them. Closed **D45**.
  - [x] **R14-a** `9e81d9e7` -- an employer contribution NAMES its funding profile (**R-SAL5**) and
        the calendar-wide projection became SINGLE (**N-443**), closing **N-533** / **N-534** with
        it. **A later step must obey**: `investment_params.salary_profile_id` is nullable and
        UNREAD; `R14-b` is its reader and owns what a NULL means at the door.
  - [x] **R14-b** `e0f0c05f` -- the contribution tier CONSUMES the paycheck engine's per-period
        breakdown instead of re-deriving it (**R-SAL2**), retiring the second spelling of a
        deduction's amount together with `_annual_cap_averaged` and `_period_capped_total`. Moved
        `+$452.42` of modelled employer money, reproduced on two independent bases. Closed **D45**,
        **N-532**. **A later step must obey**: it ships an INTERIM tail rule past the saved calendar
        whose two residues are measured (**N-541**, **R-SAL10**).

**What became of the obligations the span left.** `R14-a`'s NULL was answered at `R14-b` (an
unknown funding profile models NO employer money, developer 2026-09-04). `R14-b`'s interim tail
rule -- the hold `AccountPayrollFeed` invented past the saved calendar -- was deleted at
`salary:S3-e-2` (`salary_s3e2_as_built_2026-09-11.md`), which is where **N-541** and the four false
sentences about the rule (**N-542** to **N-546**) closed.
