> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# The shipped salary step `S11-c-2c` and its two containers, condensed to pointers on 2026-10-05

Ruling `salary:R-SAL103` ("Finished steps become pointers"): each finished salary step's full text
moves word for word to a history file and `docs/plans/implementation_plan_salary.md` keeps a
one-line pointer naming its commit. `S11-c-2c` shipped at `4447238b`, the empty commit carrying
`Ships: salary:S11-c-2c` (the code is `cb139217`, `4f59ba68`, `e1c34608`, `b12ed0e9`, `763b3a8e` and
`23482d26`), and the containers `S11-c-2` and `S11-c` ticked with it, their last leaf. The three
entries, as they stood in section 4 of that plan on the merge of the step's branch with dev
(`727602d5`), are below, carried WITHOUT re-verification against the code. Fork 9's clause, "each
stub beside the old calibrations of its date", was NOT done at the step: no stub sits on a
calibration's date on the 2026-10-05 production copy, by the coordinator's measurement, so that
comparison first runs at `S11-d`. The step's other grades are the salary lane's record (its commit
bodies and money table), not re-verified here. A live sentence may cite this record for how
something came to be, never as a plan of record.

`S11-c`, as it stood:

      - [ ] **S11-c -- the engine's calibrated path**: the DECOMPOSED parent of two leaves (the salary
            lane's decomposition, accepted by the coordinator 2026-09-23), ticking with its last.

`S11-c-2`, as it stood:

      - [ ] **S11-c-2 -- the engine prices from the stubs**: the DECOMPOSED parent of three leaves (the
            salary lane's trace, granted by the coordinator 2026-10-04), ticking with its last.

`S11-c-2c`, as it stood:

      - [ ] **S11-c-2c -- the engine prices from the stubs**: the latest switched-on stub on or before
            the payday with the SAME LINES supplies the four taxes and the formulas the difference
            (`PricedLine`'s line identity shipped at `S11-b`); with no such stub, the latest switched-on
            stub on or before it, of ANY lines, and with none at all the formulas alone (**R-SAL54**,
            settling fork 8b); the stub's side priced by the kinds it records, never its lines' current
            ones (**R-SAL58**), on its own payday's tax year (**R-SAL77**); every priced tax floored at
            `$0.00` (**R-SAL55**). Each priced paycheck names the stub that priced it, or none: the
            formulas, and the cockpit, the Taxes tab and the breakdown say so, the old calibration
            buttons becoming a link to the Pay stubs list (**R-SAL100**, `X-at-6`'s stub half). Every
            reader switched; the rates path, `calibrate_*` and the calibration schemas deleted with
            their door. **MOVES MONEY**, graded on a clone holding the stubs: fork 8c, the projected
            diff, `tests/manual/measure_r18d_phone_line.py` before and after (within a cent of the
            pair), each stub beside the old calibrations of its date (fork 9). Closes **SAL-565**.
