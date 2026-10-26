> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# 5 shipped salary steps archived under rule 5 on 2026-09-25

The salary plan stood at 300 lines, the most its 320-line cap allows under the gate's 20-line
headroom arm, when the tick for `salary:X-at-1` (`42bb425d`) split `X-at` into its leaves: their
specifications take twenty lines more than X-at's one entry did, the replaced signpost gave back
two, and these five entries' eighteen lines are the rest. Under `docs/plans/conventions.md` rule 5
that room comes from archiving COMPLETED steps, never from trimming a live specification.
These five are SHIPPED, no other row of `docs/plans/steps.md` cites any of them as a wait or an
alias (measured on the tick's tree with the gate's own `blocked_keys` / `alias_keys`), and none
owns a ledger row, so their rows leave the index and their entries leave the plan's section 4,
verbatim below. The oldest completed spans go first: the shipped parents `S3`, `R15` and `C12`,
whose leaves had already left (their records are the four `historical/salary_s3*` files,
`historical/salary_r15_as_built_2026-09-14.md` and `historical/salary_c12_as_built_2026-09-18.md`);
then the shipped leaves `S11-a` and `X-av-1` of the still-open parents `S11` and `X-av`, the shape
`docs/audits/balance_architecture/archive/shipped_steps_archived_2026-09-16.md` set for `X-au-g`'s
leaves. `S2`, older than all five, keeps its entry, because the plan's section 0 places it in the
arc's own sequencing; `S11-b`, `S11-c-1`, `X-av-2` and `R18` keep theirs, because open rows wait
on them or an outcome names them. Carried WITHOUT re-verification. A live sentence may cite one of
these ids for how something came to be, never as a plan of record.

The plan entries as they stood:

    - [x] **S3** `329b663d` -- the engine prices the WHOLE horizon (**R-SAL10**, **R-SAL11**,
          **R-SAL14**, **R-SAL15**; closed **N-541**). Its fourteen leaves left this document and the
          index 2026-09-18 (rule 5); the records are the four `historical/salary_s3*` files.

    - [x] **R15** `77901fe0` -- what a payroll deduction's own FREQUENCY means: a RECURRENCE RULE on the
          row (**R-SAL3**, **R-SAL29**-**R-SAL32**, **R-SAL35**-**R-SAL37**); ticked with `R15-c`, its
          last leaf. Its leaves `R15-a`..`R15-c` left this document and the index 2026-09-24 (rule 5);
          the span as it stood: `historical/salary_r15_as_built_2026-09-14.md`.

    - [x] **C12** `945651c2` -- one current-paycheck producer (**R-SAL25**-**R-SAL28**); closed **P62**,
          **P63**, **P64**'s engine half. Its leaves `C12-a` and `C12-b` left this document and the
          index 2026-09-24 (rule 5); as it stood: `historical/salary_c12_as_built_2026-09-18.md`.

      - [x] **S11-a** `8f744c33` -- the tables: `salary.pay_stubs` over one amount per paycheck line
            (keyed onto its own profile's line, RESTRICT), per tax (`ref.withholding_kinds`) and per
            one-off; a trigger refuses a stub's DELETE, a move of its profile and any TRUNCATE
            (**R-SAL44**, **R-SAL46**); migration `5641f7729b68`, its downgrade refusing while stubs
            exist (**R-SAL47**). `$0.00`.

      - [x] **X-av-1** `fe054204` -- one salary profile per paycheck definition in each scenario, active
            or not (**R-SAL63** as scoped by **R-SAL69**; migration `9b2c5656eed9`). `$0.00`; closed
            **N-294**, opened **SAL-570**.

The index rows:

- **S3** `329b663d` -- The DECOMPOSED parent of pricing every payday of the projected horizon, split 2026-09-05 into FIVE leaves once the RAISE MODEL fork was ruled (**R-SAL11**): the per-raise termination, the additive column, the cutover, the horizon on `project_profile`, and the deletion of `AccountPayrollFeed`'s hold, plus a SIXTH (`S3-f`) minted 2026-09-11; ticked with S3-f-4, the last leaf of that sixth.
- **R15** `77901fe0` -- The DECOMPOSED parent of "a deduction's cadence is a recurrence rule" (**R-SAL3**), split into THREE leaves 2026-09-13 once its four forks were ruled (**R-SAL29**..**R-SAL32**): the per-month ceiling as the cadence's third value, then the owning arm with the engine and the migration that drops the count, then the form; ticked with R15-c, its last leaf (**R-SAL35**: one PR for the last two).
- **C12** `945651c2` -- The DECOMPOSED parent of one current-paycheck producer, split 2026-09-12 (**R-SAL28**) at the money line once **R-SAL25**-**R-SAL27** ruled the design asked for from scratch: the NO-MONEY leaf (the engine package, the amount basis over the pass's pricer, the four byte-identical direct-engine sites) and the MONEY leaf (`/savings`).
- **S11-a** `8f744c33` -- Added the transcribed pay stub's tables (**R-SAL42**): `salary.pay_stubs` over one amount per paycheck line (keyed onto its own profile's line, RESTRICT), per tax (a new `ref.withholding_kinds`) and per one-off; a trigger refusing a stub's DELETE, a profile move and any TRUNCATE (**R-SAL44**, **R-SAL46**); migration `5641f7729b68`, its downgrade refusing while stubs exist (**R-SAL47**); `$0.00`.
- **X-av-1** `fe054204` -- Made a paycheck definition belong to at most one salary profile in each scenario, active or not (**R-SAL63** as scoped by **R-SAL69**): `uq_salary_profiles_scenario_template`, added by migration `9b2c5656eed9`, which refuses while data breaks it, its downgrade dropping it; `$0.00`. Closed **N-294**, opened **SAL-570**.
