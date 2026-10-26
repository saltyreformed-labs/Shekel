> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# salary:R18-d as performed (2026-09-19)

**The operator act that moved the phone allowance from an income template onto a paycheck line.**
`R18-d` is the fourth leaf of `R18` (a paycheck is base pay plus a list of lines, **R-SAL38**) and
the only one with no code: the developer performed it through the app's own doors (**R-HJ**) on
2026-09-19 at 09:18 EDT. **This file is the act's RECORD, and the commit that adds it is the commit
`R18-d`'s SHIPPED row cites** -- ruling **R-SAL40** (`docs/plans/rulings.md`), the developer,
2026-09-19: an operator act's record is a commit; the same shape serves every later operator act.

## What production holds after the act (read at 09:32 EDT on the production database, read-only)

* `salary.paycheck_lines` row 13: `Phone Allowance`, `$45.00`, kind `taxable_earning`, calc method
  1, active, created `2026-09-19 13:18:43 UTC`; its rule `budget.recurrence_rules` row 63: unit
  `period`, interval 1, placement `containing_date`, `starts_on 2026-09-10`, no end, `max_per_month 1`
  -- "the first paycheck of every month", **R-SAL29**'s ceiling, from the 2026-09-10 period.
* The Phone Allowance income template (`budget.transaction_templates` 3, rule 3: `month` / 1 /
  `period_starting_on_or_after`, `starts_on 2026-03-01`) now ENDS `2026-09-18` (audit 12395,
  13:17:00 UTC). Its seven rows: March Cancelled, April Received `$39.54`, May Received `$39.54`,
  June Received `$39.54`, July Cancelled, August Received `$39.54`, **September Received `$39.54`
  on the 2026-09-10 period (row 794)** -- records, untouched. **The end HARD-DELETED its 23
  projected rows** (795..2843, `$39.54` each, one per first-of-month payday through 2028-08;
  audit 12396-12418), the rows the cash fold had counted.
* The 2026-09-10 salary row (`Data Manager`, row 1631) is Received at `$2,572.78`, a record; the
  2026-09-24 and 2026-10-08 salary rows are Projected. **A third write rode the line's save**: the
  salary template's `default_amount` was re-stated `$2,572.78` -> `$2,613.51` (audit 12421;
  `salary_regeneration.py` prices the period containing today, the settled one) -- the figure the
  full-edit popover's Estimated box shows beside the record; no balance reads it.
* **After this measurement the developer REPLACED the calibration** (audit 12422 DELETE row 3 at
  13:56 UTC, 12424 INSERT row 4 at 13:59, the same 2026-08-27 stub re-read: gross `$3,631.70`, the
  four effective rates moved in the fourth decimal). That is **N-535**'s "the door REPLACES" firing
  again, in the developer's own hands; the line's `$40.73` is unchanged by it to the cent.
* **The act differs from the runbook's "end as of August 2026, start 2026-09-01", and the
  difference is correct**: September's allowance was already paid and recorded on the 09-10
  paycheck through the template, so the template ends after it and the line starts with that same
  period; the line's September occurrence changes only the projected ESTIMATE of a settled paycheck,
  which nothing counts, and nothing is counted twice.

## What moved (the pre-act 07:21 restore `shekel_xbi4b` against the 09:32 restore `shekel_r18d`, both at `97f92340fffc`; `tests/manual/measure_r18d_phone_line.py`, read-only through `income_service.paycheck_pricing`, the ONE producer)

* **24 priced figures move: the 23 projected first-of-month paydays from 2026-10-08 through
  2028-08-10** (the saved horizon: three in 2026, twelve in 2027, eight in 2028) **plus the
  2026-09-10 payday's ESTIMATE**, whose Received record does not move. Gross `+$45.00` on each
  (base `$3,631.74`, then the raise tiers `$3,722.53` / `$3,834.21` / `$3,930.07` / `$4,047.97`),
  net `+$40.72` x12 / `+$40.73` x4 / `+$40.74` x8 (the withholding rounds). Every other payday
  `$0.00`; no settled row moves.
* **What the BALANCE moved is the gap, not the line**: the 23 projected `$39.54` template rows left
  with the template and the 23 paychecks gained `$40.72`-`$40.74`, so the projected checking
  balance moves by `+$1.18`..`+$1.20` a month, about `+$27.33` over the horizon -- SAL-564's
  figure, on the plan tier only.
* **The developer checked the 2026-10-08 paycheck** (`$2,613.51`, `+$40.73`) and the months through
  February 2027, and reported the figure WRONG against the stub: **the `$45.00` allowance nets
  `$39.54` on the real paycheck** (`$5.46` withheld, 12.13%), which is what the retired template row
  carried. The engine withholds a taxable line at the calibration's four whole-paycheck EFFECTIVE
  rates (federal 0, state 2.874%, SS 5.355%, Medicare 1.252%: 9.481%, `$4.27` on `$45`); the
  employer withholds the marginal dollar at the statutory rates the app already stores (SS 6.2%,
  Medicare 1.45%, NC 3.99% for 2026: 11.64%, `$5.24`) or close to them. `+$1.19` a month against
  the stub, `+$14.28` a year. Filed as **SAL-564**, a fork for the developer; the line stays as
  entered until it is ruled.

## The entry, verbatim

  - [ ] **R18-d** the OPERATOR runbook: Josh ends the Phone template as of August 2026 and enters
        the `$45.00` taxable line, monthly first paycheck, start 2026-09-01. **MOVES MONEY**.
