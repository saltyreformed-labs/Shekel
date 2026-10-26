> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# salary:R18 as built (2026-09-23)

**A paycheck is BASE PAY plus a LIST OF LINES.** The `R18` span of `implementation_plan_salary.md`
as it stood when the `S11-a` / `S11-b` tick of 2026-09-23 moved it here under conventions.md rule 5
as a COMPLETED span: the container had ticked with its last leaf, `R18-d` (`0345fbae`), and the
document stood at 300 of 320 lines, exactly the gate's 20-line headroom floor, with two shipped
leaves to tick. Carried verbatim and NOT re-verified against the code. The leaves' full
records are their commit messages (`ef0dc831`, `ad9fed61`, `34ad4bda`, `0345fbae`) and, for the
operator act, `historical/salary_r18d_as_performed_2026-09-19.md`.

## The span, verbatim

- [x] **R18 -- a paycheck is BASE PAY plus a LIST OF LINES.** `0345fbae` -- ticked with R18-d, its
      last leaf (finding **D59** closed; ruling **R-SAL38**, six forks, 2026-09-15): the DECOMPOSED
      parent, R-SAL35's shape, four leaves. A line's kind is its position in the waterfall (taxable
      earning, pre-tax deduction, post-tax deduction, after-tax earning); a percentage line is a
      percentage of BASE PAY, never of gross (worked: base `$3,631.74`, +`$45` taxable, 6% of base
      `$217.90` against `$220.60` of gross). One deposit is one app row (`bank_import:X-gj-3a`).
  - [x] **R18-a** `ef0dc831` -- the storage rename (`paycheck_lines`, `paycheck_line_kinds`;
        migration `0a4d2c3e89f8`), byte-identical over the 64 saved paychecks; `$0.00`.
  - [x] **R18-b** `ad9fed61` -- the two earning kinds (migration `6c15d2a97b78`), the engine's one
        line pass (`priced_gross`; a percentage line is % of BASE), the line door, the cockpit
        groups; byte-identical over the 64 saved paychecks; opened **SAL-561**.
  - [x] **R18-c** `34ad4bda` -- every line's start and optional end on its own rule (R-SAL30 /
        R-SAL31 amended; no migration, no engine change: the door was missing); the drive
        `d97ca1b5`; `$0.00`. Surfaced the blank-start question -> **R-SAL39**, **SAL-562**, `S9`.
  - [x] **R18-d** `0345fbae` -- the OPERATOR act, performed 2026-09-19: the Phone template ends
        2026-09-18 after its Received September row; the `$45.00` taxable line runs from the 09-10
        period, first paycheck of a month. **MOVED MONEY** (23 projected paychecks `+$40.73`; the
        stub nets `$39.54`, **SAL-564**). Record:
        `historical/salary_r18d_as_performed_2026-09-19.md`.
