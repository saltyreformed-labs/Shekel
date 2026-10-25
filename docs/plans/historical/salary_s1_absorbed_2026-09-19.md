> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# salary:S1 ABSORBED into S11 (2026-09-19)

**Ruling `salary:R-SAL41`, developer 2026-09-19, amending `R-SAL9`.** `S1` (rank #41, "a calibration is a
DATED OBSERVATION and is never destroyed") is removed from the index: its three clauses -- an effective date on
the row backfilled at its stub's date, the deleted calibrations RESTORED from `system.audit_log` as dated rows,
and the door made to ADD rather than replace -- stand inside `S11`, where a calibration becomes the stub
transcribed line by line. Nothing of `S1` is refused; what changes is the row a stub is stored as. Its three
findings **N-441**, **N-535** and **N-530** are `S11`'s. The developer's own act of 2026-09-19 (audit 12422 /
12424: the 2026-08-27 calibration deleted and re-entered) is a second instance of N-535 beside the 2026-08-28
one, so `S11`'s restore names TWO deleted rows. The entry as it stood, verbatim:

- [ ] **S1 -- a calibration is a DATED OBSERVATION and is never destroyed** (**R-SAL9**, amending
      **R-SAL4**; findings **N-441**, **N-535**, and **N-530**'s calibration kind).
      `salary.calibration_overrides` carries effective rates derived from ONE stub on ONE date and
      stores no date, so entering a stub restates every paycheck the owner ever had: `+$29.09` on
      each of seven RECEIVED paychecks, visible since `balance:X-au-d` made a settled row's plan a
      derivation. **And the write door REPLACES**, so the stub that priced the eleven earlier
      paychecks is already gone (**N-535**, measured at S2). Three parts, ruled 2026-09-04: an
      effective date on the row, backfilled at its stub's date; the calibration deleted 2026-08-28
      RESTORED from `system.audit_log` id 4212's `old_data` as a second dated row; and the door made
      to ADD rather than replace. The engine then resolves the calibration in force for each period
      as it resolves a raise, and all 12 settled paychecks re-derive to their generated figure.
      **Dating the survivor ALONE was rejected**: it resolves no calibration before 2026-08-27 and
      costs `-$334.32` on the 2026 net total the projection page and the cockpit show.
      **It can never move a balance** (a settled row is worth what it recorded); what moves is the
      EXPECTED figure and the variance beside it. The row's "derived effective rates" that nothing
      recomputes (**N-530**) are decided here: the rates derive from the stub's stated figures at
      read, or the stub's figures are the stored fact and the rates go (**balance:R-IY**). A
      migration; own review pass.
