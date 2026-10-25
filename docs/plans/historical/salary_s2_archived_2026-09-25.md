> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# The shipped salary step `S2` archived under rule 5 on 2026-09-25

The salary plan stood at 300 lines, the most its 320-line cap allows under the gate's 20-line
headroom arm, when the tick for `salary:X-at-4` (`5d5f5bc1`) ticked that leaf, minted
`salary:X-at-8` (the 2027 tax law) and gave `X-at-3`'s specification the two-state wording clause. Under
`docs/plans/conventions.md` rule 5 the room comes from archiving a COMPLETED step, never from
trimming a live specification. `S2` was the one SHIPPED step left in the plan that no other row of
`docs/plans/steps.md` cites as a wait or an alias and no outcome names (measured on the tick's tree
with the gate's own `blocked_keys` / `alias_keys` and the outcomes table), and it owns no ledger
row. The tick for `salary:X-at-1` kept it, on the plan's section 0 placing it in the arc's own
sequencing (`historical/salary_shipped_steps_archived_2026-09-25.md`); a shipped step no longer has
a place in that sequence, and rule 5's second condition (no live sentence may depend on an archived
one) is met by moving the sentence and the clause of section 0 that placed it here with it. Its
entry, its index row and that paragraph as it stood are below, verbatim, carried WITHOUT re-verification. The as-built
record of the step itself is `historical/salary_s2_as_built_2026-09-04.md`. A live sentence may cite
`S2` for how something came to be, never as a plan of record.

The plan entry as it stood (section 4):

    - [x] **S2** `08638f61` -- the `-$19.28` was a DELETED calibration, not the engine; no single
          calibration reproduces the record. Closed **N-442**, opened **N-535**, ruled **R-SAL9**.
          As-built: `historical/salary_s2_as_built_2026-09-04.md`.

Section 0's paragraph as it stood; the sentence and the clause naming `S2` left it, and its other
words stay in the plan:

    **Why each step sits where `steps.md` puts it, which is that table's to say and not this
    document's.** `S2` is the arc's cheapest first act, because a derivation that moves `-$19.28` for no
    recorded reason is a baseline nobody can measure `S1` against until the input is named. The
    earnings-lines chain follows the bank_import production scope by `bank_import:R-JJ`, because `R18`
    is what makes one payroll deposit one app row; `S2` sits ahead of it as the arc's first act. `C12-a`
    decided the shape `balance:X-i1` waited on (**R-SAL27**): the basis takes the pass's PRICER.

The index row:

- **S2** `08638f61` -- Named the input that moved a past paycheck `-$19.28`: a `calibration_overrides` row DELETED 2026-08-28 in the act that inserted the live one (`system.audit_log` 4212/4213). Eleven settled paychecks re-derive `$0.00` under it and the twelfth only under the live one, so NO single calibration reproduces the record -- **R-SAL4** measured. Refuted `balance:X-aw`. Closed **N-442**, opened **N-535**.
