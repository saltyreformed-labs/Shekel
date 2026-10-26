> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# salary:S3 as built (2026-09-13)

**The engine prices the WHOLE horizon.** The `S3` span of `implementation_plan_salary.md` as it
stood the moment `S3-f-4` (`329b663d`) shipped and the `S3` and `S3-f` containers ticked
with it -- moved here under conventions.md rule 5 because that document would have stood at
301 of 320 lines with the tick applied, inside the gate's 20-line headroom. The three `[ ]`
entries below (`S3`, `S3-f`, `S3-f-4`) are `[x]` at `329b663d`. The `S3` container's
argument and the `S3-a`..`S3-d` and `S3-e-1` records are `salary_s3_leaves_as_built_2026-09-11.md`;
the `S3-f` leaves' record is `salary_s3f_as_built_2026-09-13.md`; `S3-e-2`'s is
`salary_s3e2_as_built_2026-09-11.md`.

## The span, verbatim

- [ ] **S3 -- the engine prices the WHOLE horizon** (the DECOMPOSED parent, split 2026-09-05 into
      five leaves once **R-SAL11** ruled the raise model; **R-SAL10**; closed **N-541**; **N-540**
      went to `S4`): the extrapolation `AccountPayrollFeed` held past the saved calendar is DELETED
      rather than repaired, `income_service` prices a paycheck PER PAYDAY on demand (**R-SAL14**,
      `S3-d`) and the feed prices whichever period it is handed through that pricer (**R-SAL15**,
      `S3-e-2`). Every leaf but `S3-f-4` has shipped; the container ships with it. Its full
      argument, the struck cost figures and the raise-model fork R-SAL11 closed are archived with
      the leaves.
- [x] **S3-a** `e4491ee6` -- the merit horizon is a per-raise TERMINATION, not a split. Archived.
- [x] **S3-b** `8a8dd51e` -- `terminal_year` and three CHECKs, migration `c9a4e17b53d8`, no reader;
      its all-NULL obligation was discharged by `S3-c`'s backfill. Archived with `S3-a`.
- [x] **S3-c** `62567d87` -- THE CUTOVER (**R-SAL12**, **R-SAL13**): the stored end year is a
      raise's only termination. **Its downgrade is STATE-LOSSY**: a lossless rollback needs
      `UPDATE salary.salary_raises SET terminal_year = NULL` beside it. Archived with `S3-a`.
- [x] **S3-d** `62612c9a` -- the producer became a FUNCTION of the payday (**R-SAL14**), NO FIGURE
      MOVED; its not-yet-asked-for obligation was discharged by `S3-e-2`; opened **N-547**.
      Archived.
- [ ] **S3-f -- the PER-RAISE probe and its Save on the `/retirement` rail**
      (**R-SAL20**-**R-SAL24**; four leaves, the middle one split at the money line; only `S3-f-2a`
      moved a figure); ships with `S3-f-4`. As built so far, with every shipped leaf's record:
      `historical/salary_s3f_as_built_2026-09-13.md`.
- [x] **S3-f-1** `c463dfbc` -- the engine seam (**R-SAL20**); NO FIGURE MOVED. Archived with `S3-f`.
- [x] **S3-f-2** `587c20d5` -- the plan point and the probe, split at the money line. Archived with
      `S3-f`.
- [x] **S3-f-2a** `f3032c87` -- the calibrated current paycheck (**R-SAL21**); MOVED `+$41,562.00`.
      Archived with `S3-f`.
- [x] **S3-f-2b** `587c20d5` -- the point believes each raise's end year; the rail probes it.
      Archived with `S3-f`.
- [x] **S3-f-3** `a5ef1bdf` -- the rail SAVES it (**R-SAL22**); the regeneration is a service
      (**R-SAL24**). Archived with `S3-f`.
- [ ] **S3-f-4** -- the refused probe RENDERED: the readiness GET re-renders the rail with the
      message on the raise's row in the one 422 shape the Save uses, and the schema's nested shape
      goes. Minted 2026-09-13 from `S3-f-2b`'s reviews (**SAL-548**); the container ships with it.
- [x] **S3-e** `a6af5b3c` -- the hold is DELETED (the DECOMPOSED parent, split 2026-09-06 into the
      NO-MONEY re-homing and the MONEY; **R-SAL16** carries the argument and **R-SAL15** the
      design). Both leaves shipped, so the container ships with the last of them.
- [x] **S3-e-1** `b8ee429a` -- the two window-only questions re-homed and deleted (**R-SAL17**,
      **R-SAL18**); NO FIGURE MOVED. Archived with the S3 leaves.
- [x] **S3-e-2** `a6af5b3c` -- the feed prices a payday ON DEMAND through the pass's pricer
      (**R-SAL15**), its resolvers take the PERIOD and carry both presence facts (**R-SAL19**), and
      the timeline asks the period whether it is projected (**R-SAL18**). MOVED `+$194,321.85` on
      `/investment`'s 40-year contribution line and `-$4,909.81` on `/retirement`, measured
      2026-09-11. Closed **N-541**-**N-546**. As built:
      `historical/salary_s3e2_as_built_2026-09-11.md`.
