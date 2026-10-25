> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# salary:S3-f as built so far (2026-09-13)

**Every recurring raise's end year is a fact ON THE RAISE, the `/retirement` assumptions rail
probes it and saves it, and the salary regeneration is a service.** The DECOMPOSED parent of the
per-raise probe and its Save, split 2026-09-11 into three leaves on **R-SAL23** once **R-SAL20**,
**R-SAL21** and **R-SAL22** ruled the design asked for from scratch; its middle leaf split again
2026-09-12 at the money line (**R-SAL6**'s rule). A probed raise is a VALUE carrying the row's
terms with the end year changed -- an INPUT for one request, the row the one home -- not the
fabrication `S3-c` deleted. All six leaves have shipped, the last of them `S3-f-4` at
`329b663d` (the refused probe rendered, **SAL-548**), with which the container and its parent
`S3` shipped; every leaf's record is below. Condensed here from
`implementation_plan_salary.md` under conventions rule 5 by `S3-f-3`'s tick (developer ruling
2026-09-12: archive the shipped span rather than raise the cap).

## The leaves

- **S3-f-1** `c463dfbc` -- the engine seam (**R-SAL20**): `RaiseTerms` is the engine's contract
  with a raise, `PayrollBasis.raises` is always that value, the pricer keys its memo on the
  canonical set, `load_payroll_feeds` is wiring plus a per-raise-set build. NO FIGURE MOVED
  (X-bl control byte-identical). Its obligation -- `retirement_dashboard_service` spelled a raise
  type's name beside `SalaryRaise.raise_type_name` -- was discharged at `S3-f-2b`.
- **S3-f-2** `587c20d5` -- the plan point and the probe (the DECOMPOSED parent, split 2026-09-12
  when **R-SAL21** was MEASURED to move the stored verdict: `/retirement`'s current-pay door priced
  with no calibration, so one calibrated producer was a money leaf and the probe followed it).
  Both leaves shipped, so the container shipped with the last of them.
- **S3-f-2a** `f3032c87` -- `/retirement` prices its current paycheck through the pass's pricer,
  calibrated (**R-SAL21** as amended): `_compute_current_pay` and `_CurrentPay` went, the
  paycheck is derived per plan point and the gap scales by the engine's own take-home rate.
  MOVED the 2026-09-10 net `$2,541.49 -> $2,572.78` and required savings
  `$1,120,707.00 -> $1,162,269.00` on the developer's data; `/savings` and `/investment` did not
  move. Closed **N-547** (the payday priced 2 -> 1 times) and **P62**'s `/retirement` half.
- **S3-f-2b** `587c20d5` -- the point and the probe: `PlanPoint.raise_end_years` (canonical;
  `terms_for` feeds every salary-path read), `plan_with(raise_probes=...)` resolving the rail's
  pairs against the rows by the ONE rule `salary_raises.end_year_of` (all three **R-SAL22**
  clauses; the salary schema calls it too); each recurring-raise rail row became the salary form's
  end-year pair (**R-SAL13**'s mode, authoritative) sending the what-if. NO STORED FIGURE MOVED.
- **S3-f-3** `a5ef1bdf` -- the Save (**R-SAL22**, **R-SAL24**): each rail row is a form posting the
  SAME pair its what-if sends to `retirement.update_settings`; `RetirementSettingsSchema` gathers
  it as the readiness query does; the pair is resolved against the rows the rail LISTS
  (`recurring_raises`; anything else is a 404 with `log_refused_lookup`'s trail), graded by
  `end_year_of` against the row (a designed 422 with the message on the row's control), written
  on the row, and followed by the regeneration every raise write runs. That regeneration moved
  below the route layer as `app/services/salary_regeneration.py` (**R-SAL24**): Flask-free,
  taking the read pass and RETURNING the retained ids; the salary package keeps a thin adapter
  under the old name; the direct-engine census moved its entry with it. A stale race re-renders
  the rail fresh at 409 with the conflict on the row. NO STORED FIGURE MOVED. What the
  regeneration keeps in step, stated exactly: the rows' amounts are DERIVED at read time since
  `balance:X-au-d` (rule 2 prices a paycheck from the profile's raise rows), so what a Save
  without it would have left stale is the template's `default_amount` -- read when the profile is
  archived -- and the row set, not every grid figure.

- **`S3-f-4`** `329b663d` -- the refused what-if RENDERED (**R-SAL33**, **R-SAL34**). Every
  refusal of `GET /retirement/readiness` is the assumptions rail re-rendered at 422 through the
  ONE renderer the Save uses, `{control: [messages]}`, the query string echoed, delivered to
  `#assumptions-region` by htmx's `HX-Retarget` because the trigger targets the readiness card,
  which keeps the picture it last drew; the schema's nested JSON shape is gone and
  `RaiseProbeError.errors` carries which half was refused. `retirement_controls.js` retires a
  row's refusal on the first edit of either of its what-if controls, aborts the request in flight
  on every keystroke (`abortInFlightRefresh`; the trigger also carries `hx-sync="this:replace"`
  for the next request) and restores the caret after a swap (Chromium put it at the start).
  Graded by `tests/manual/verify_retirement_rail_refusal.py`, 28 browser checks, green on a
  clone 2026-09-13 (the lane reports three runs). NO STORED FIGURE MOVED. Closed **SAL-548**; opened **SAL-550**
  (refusals outside the rail still render nowhere) and **SAL-551** (the SWR message is in the
  fraction domain), both to `S5`. `app/routes/retirement.py` stands at 944 of 1000 lines.

## What the span left open

- ~~The readiness GET's refusal of a probe is still a JSON 422 the page never renders~~ -- closed
  by `S3-f-4`; what it left is **SAL-550** and **SAL-551**.
- The rail lists every ACTIVE profile's recurring raises, which is not exactly the set the page
  projects in the multi-profile case (the `S3-c` review's known weaker claim).
- The rail's raise row carries no `version_id`, so a Save from a rail rendered before a
  salary-page edit of the same raise is last-write-wins on ONE column; the salary form's
  stale-form pre-check refuses the same race on its whole payload. A developer question.
- `routes/salary/_helpers._regenerate_all_salary_transactions` builds one read pass PER PROFILE;
  one pass handed to the service per profile is the shape `R-SAL24` makes available.
- `projection_inputs.py` stands at 992 of 1000 lines; the next edit there splits it first.
