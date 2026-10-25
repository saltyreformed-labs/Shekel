> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# salary:S3-a, S3-b, S3-c and S3-d as built (archived 2026-09-11)

The four shipped leaves of `S3` that precede `S3-e` and `S3-f`, condensed out of
`implementation_plan_salary.md` at `S3-f-1`'s registry pass under conventions rule 5
(the document had reached its 320-line cap). Their bodies, verbatim as the plan carried
them; the commits are the record, and the rulings are `rulings.md` rows `R-SAL11`-`R-SAL14`.

- [x] **S3-a** `e4491ee6` -- the merit horizon is a per-raise TERMINATION, not a split.
- [x] **S3-b** `8a8dd51e` -- `terminal_year` and three CHECKs, migration `c9a4e17b53d8`, no backfill
      and no reader. **A LATER LEAF MUST OBEY**: the column is LIVE to the engine the moment it
      exists, `apply_raises` having probed it by `getattr` since S3-a, so what keeps figures unmoved
      is that it is all-NULL.
- [x] **S3-c** `62567d87` -- THE CUTOVER: the stored end year is a raise's only source of
      termination, `_terminate_after_horizon` and `merit_raise_horizon_years` gone, migration
      `d4e8b1c62f07` BACKFILLS (**R-SAL12**) and a new recurring raise is asked its span with no
      default (**R-SAL13**). **A LATER STEP MUST OBEY**: the downgrade is state-lossy, so a lossless
      rollback needs `UPDATE salary.salary_raises SET terminal_year = NULL` beside it; the deploy
      script's own rollback is dump-and-restore.
- [x] **S3-d** `62612c9a` -- the producer became a FUNCTION of the payday, with no horizon parameter
      (**R-SAL14**); `PeriodInfo` gained the PAYDAY, `project_profile` is deleted, and NO FIGURE
      MOVED. **A LATER LEAF MUST OBEY**: the capability is BUILT and not yet ASKED FOR -- every
      caller still passes `calendar.saved()`, so the hold is still reached and
      `TestThePricerAnswersPastTheSavedHORIZON` grades what `S3-e` needs. It did NOT close **P63**,
      did not carry **N-540**, and opened **N-547**.

## The `S3` container's entry, as the plan carried it until this pass

- [ ] **S3 -- the engine prices the WHOLE horizon** (the DECOMPOSED parent, split into five leaves
      2026-09-05 once **R-SAL11** ruled the raise model) (**R-SAL10**; closes **N-541**, carries
      **N-540**). `AccountPayrollFeed` holds a figure past the saved calendar because nothing prices
      a payday past it, and six rules over that fold were each measured wrong, so the remedy is to
      DELETE the extrapolation rather than to find a seventh (**R-SAL10**). `income_service` prices
      a paycheck PER PAYDAY on demand (**R-SAL14**, shipped at `S3-d`) rather than returning a list
      somebody has to size, and the feed prices whichever period it is handed through that pricer
      (**R-SAL15**, shipped at `S3-e-2`) -- the *lookup that RAISES past the horizon* this entry
      first specified was refused there, and `_year_averages`, `_complete_years`, `_held_employee`,
      `_held_gross` and `salary_basis(beyond=)` went with the hold. *A cost of `88` microseconds a
      payday and `92` ms per profile per render stood here for `project_profile`, the producer
      `S3-d` DELETED; it is struck rather than restated, because pricing on demand costs what the
      caller reads and there is no longer one figure to quote. `S3-e` measures the ask it
      introduces.* **AN OPEN DEVELOPER FORK BOUNDS THIS STEP'S CENSUS**: which RAISE MODEL the
      engine applies past the saved calendar is unruled -- the paycheck engine compounds a recurring
      merit raise forever and `/retirement` applies a merit horizon, and over 41 years the two
      shipped functions diverge to `2.81x`, worth `$303,121.02` on a 5%-of-gross employer
      contribution. The deletion census is AMENDED onto this entry once he rules the model; the
      sentence above holds under every candidate. **MOVES MONEY**; own review pass, own harness.

## `S3-e-1`'s entry, as the plan carried it until this pass

- [x] **S3-e-1** `b8ee429a` -- the two window-only questions re-homed and both members deleted: the
      transfer-average boundary in `build_contribution_timeline` became the caller's `saved_through`
      off `PayCalendar.horizon()`, and `_plan_for`'s gate became `is_payroll_linked` (**R-SAL17**).
      **`prices()` was in NO census** and is why this leaf existed. NO FIGURE MOVED. **R-SAL18**
      binds `S3-e-2` to `DerivedPeriod.is_projected`.
