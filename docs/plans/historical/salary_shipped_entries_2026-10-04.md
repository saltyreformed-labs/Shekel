> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Seven shipped salary steps' plan entries, condensed to pointers under R-SAL103 on 2026-10-04

The salary plan stood at 300 lines, the most its 320-line cap allows under the gate's 20-line
headroom arm, when `salary:S11-c-2a`'s tick needed about 16 more: `S11-c-2` split into three leaves,
the new step `S16`, and `X-at-6` re-worded. Every shipped salary step was still a wait of an open
`docs/plans/steps.md` row, so none could leave the plan whole. The developer ruled `salary:R-SAL103`
("Finished steps become pointers"): each finished step's full text moves word for word to a history
file and the plan keeps a one-line pointer naming its commit. Six of the entries below are verbatim
as they stood in section 4 of `docs/plans/implementation_plan_salary.md` on dev `8db709aa6`;
`S11-c-2a`'s was written at its own tick and comes straight here. All seven are carried WITHOUT
re-verification against the code. A live sentence may cite this record for how something came to be,
never as a plan of record.

`S11-b`, as it stood:

      - [x] **S11-b** `1d3a2574` -- the door: the payday picked first (**R-SAL50**), one the app holds
            up to the next (**R-SAL48**, **R-SAL49**; a kept date unchecked, **R-SAL53**), a new stub on
            a payday already holding one refused (**R-SAL52**), the printed net checked, one-off clashes
            refused (**R-SAL45**, **R-SAL51**), the comparison and the switch; `delete_line` refuses a
            named line. `$0.00`; opened **SAL-567**, **SAL-568**.

`S11-c-1`, as it stood:

      - [x] **S11-c-1** `dff66c5e` -- a stub line records the kind it is printed under (**R-SAL58**,
            retiring **R-SAL56**; migration `9b64df71cc34`), and the one-off clash asks only what a save
            adds (**R-SAL57**). `$0.00`; closed **SAL-567**.

`S11-c-2a`, as written at its tick:

      - [x] **S11-c-2a** `cf2e59a4` -- the stub door asks the gross the stub prints (**R-SAL99**), typed
            once, checked against base pay plus every taxable earning by the stub's own kinds, never
            stored. `$0.00`, no migration; closed **SAL-590**, opened **SAL-592**.

`R18`, as it stood:

    - [x] **R18** `0345fbae` -- a paycheck is BASE PAY plus a LIST OF LINES (**R-SAL38**, six forks);
          closed **D59**. Its leaves `R18-a`..`R18-d` left this document and the index 2026-09-24 (rule
          5); the span as it stood: `historical/salary_r18_as_built_2026-09-23.md`.

`X-av-2`, as it stood:

      - [x] **X-av-2** `89a56168` -- one engine walk for base pay at each payday's own rhythm
            (**R-SAL66**, **R-SAL70**); closed **SAL-569**.

`X-av-3a`, as it stood:

      - [x] **X-av-3a** `8e832d8e` -- the switch: `salary.pay_entries`, migration `70680a4a7405` (one
            entry per profile, `annual_salary` dropped), the engine's walk (**R-SAL82**), the pay-change
            banners (**R-SAL84**, **R-SAL85**, **R-SAL89**), create and Fix (**R-SAL90**, **R-SAL93**).
            On the 2026-09-25 05:59 production copy, 104 projected paychecks move by one cent, the first
            on 2029-07-12, and no settled record moves; a rollback prices 130 a cent below their
            pre-release figure (**R-SAL92**). Closed **N-391**'s app half, **SAL-572**, **D44**.

`X-at-1`, as it stood:

      - [x] **X-at-1** `42bb425d` -- the law's one home, `app/tax_law/`, each year citing its sources,
            read with no query and written by no app door (**R-SAL74**; the tests' law, **R-SAL80**).
            `$0.00`, byte-identical on a production clone; closed **N-236**, **SAL-574**.
